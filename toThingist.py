#!/usr/bin/env python3

"""toThingist - sync between Todoist and Cultured Code's Things"""

import argparse
import configparser
import json
import logging
import os
import os.path
import sys
import tempfile

import todoistinterface

import thingsinterface

LOG = logging.getLogger("tothingist")


# Bumped whenever the meaning of the stored IDs changes.
# * v1: Updated for todoist API v1 IDs. Not backwards compatible with
#   older state files lacking this flag due to changes to todoist's API.
STATE_VERSION = 1


def new_state():
    """Return an empty sync state mapping.

    Subtasks a mapping of their own as Things checklists are not
    todos, but todoist subtasks are. An ID can/will appear in both.
    """

    return {"version": STATE_VERSION,
            "todoist_to_things": {}, "things_to_todoist": {},
            "todoist_to_things_subtasks": {},
            "things_to_todoist_subtasks": {}}


def has_mappings(state):
    """Return True if the state holds at least one todo mapping."""

    return any(state[key] for key in
               ("todoist_to_things", "things_to_todoist",
                "todoist_to_things_subtasks", "things_to_todoist_subtasks"))


class ToThingist(object):

    def __init__(self, todoist_obj, things_obj, things_location, state,
                 dry_run=False):
        self.todoist = todoist_obj
        self.things = things_obj
        self.things_location = things_location
        self.state = state
        self.dry_run = dry_run
        self._closed_things_ids = None
        self._unclosable_checklist_items = []

        # used when doing a dry run, where no parents will be
        # available for created sub-objects
        self._dry_run_todoist_parents = set()
        self._dry_run_things_parents = set()

    def closed_things_ids(self):
        """Return the IDs of Things todos that were already closed.

        Reading the Things logbook is the expensive part of a sync, so
        it is done once. The snapshot is taken before anything is
        changed, which is what both directions want: a todo this run
        closes in Things was only closed because Todoist had closed it
        already.
        """

        if self._closed_things_ids is None:
            self._closed_things_ids = {
                todo["uuid"] for todo in self.things.get_closed_todos()}
        return self._closed_things_ids

    def sync_things_to_todoist(self):
        """
        Sync the Things location to the ToDoist inbox.

        """

        inbox_id = self.todoist.get_inbox_id()

        # Todos ToDoist has already closed need no closing again -
        # without this, every todo closed in the last 90 days would be
        # completed in ToDoist over and over, once per run.
        closed_in_todoist = {str(todo.id) for todo
                             in self.todoist.get_completed_todos(inbox_id)}

        # Closing a todo takes it out of its list, so completions are
        # picked up from the logbook rather than from the location.
        for todo in self.things.get_closed_todos():
            todoist_id = self.state["things_to_todoist"].get(todo["uuid"])
            if not todoist_id or todoist_id in closed_in_todoist:
                continue

            if self.dry_run:
                LOG.info("[dry-run] Would mark task '%s' as complete in"
                         " ToDoist", todo["title"])
            else:
                self.todoist.set_complete(todoist_id)
                LOG.info("Marking task '%s' as complete in ToDoist",
                         todo["title"])

        things_todos = self.things.get_todos(self.things_location)

        for todo in things_todos:
            if todo["uuid"] in self.state["things_to_todoist"]:
                LOG.debug("Todo %s (\"%s\") synced already",
                          todo["uuid"], todo["title"])
                continue

            if self.dry_run:
                LOG.info("[dry-run] Would create ToDoist todo '%s' in the"
                         " inbox", todo["title"])
                self._dry_run_things_parents.add(todo["uuid"])
                continue

            new_todo = self.todoist.create_todo(todo["title"], inbox_id)
            todoist_id = str(new_todo.id)
            self.state["todoist_to_things"][todoist_id] = todo["uuid"]
            self.state["things_to_todoist"][todo["uuid"]] = todoist_id

        # Done after the loop above so that checklists hanging off todos
        # created by this very run have a Todoist parent to go under.
        self.sync_things_subtasks_to_todoist(things_todos, closed_in_todoist)

        # TODO better return
        return self.state

    def sync_things_subtasks_to_todoist(self, things_todos, closed_in_todoist):
        """
        Sync the checklists of synced Things todos to Todoist subtasks.

        Only consider open todos - a closed todoist todo completes all
        subtasks, and we consider checklists with a completed owner
        todo to be the same.

         things_todos: todos in the Things location, as returned by
          ThingsInterface.get_todos().
         closed_in_todoist: IDs already-closed todoist todos, to avoid
          continuously closing the same totos.

        """

        for todo in things_todos:
            parent_id = self.state["things_to_todoist"].get(todo["uuid"])
            if not parent_id:
                # Either this is a dry run and the todo was reported
                # rather than created, or create_todo() could not find
                # the todo it had just made and has said so.
                if todo["uuid"] in self._dry_run_things_parents:
                    self._report_subtasks_of_unmade_parent(todo)
                continue

            for item in self.things.get_checklist_items(todo["uuid"]):
                closed = (item["status"] in
                          thingsinterface.CLOSED_CHECKLIST_STATUSES)
                todoist_id = self.state["things_to_todoist_subtasks"].get(
                    item["uuid"])

                if todoist_id:
                    if not closed or todoist_id in closed_in_todoist:
                        continue

                    if self.dry_run:
                        LOG.info("[dry-run] Would mark subtask '%s' as"
                                 " complete in ToDoist", item["title"])
                    else:
                        self.todoist.set_complete(todoist_id)
                        LOG.info("Marking subtask '%s' as complete in ToDoist",
                                 item["title"])
                    continue

                # don't sync already-closed unsynced tasks
                if closed:
                    continue

                if self.dry_run:
                    LOG.info("[dry-run] Would create ToDoist subtask '%s'"
                             " under '%s'", item["title"], todo["title"])
                    continue

                new_todo = self.todoist.create_todo(item["title"],
                                                    parent_id=parent_id)
                todoist_id = str(new_todo.id)
                self.state["todoist_to_things_subtasks"][todoist_id] = \
                    item["uuid"]
                self.state["things_to_todoist_subtasks"][item["uuid"]] = \
                    todoist_id

    def _report_subtasks_of_unmade_parent(self, todo):
        """Report the Todoist subtasks a dry run would have created."""

        for item in self.things.get_checklist_items(todo["uuid"]):
            if item["status"] in thingsinterface.CLOSED_CHECKLIST_STATUSES:
                continue
            LOG.info("[dry-run] Would create ToDoist subtask '%s' under"
                     " '%s'", item["title"], todo["title"])

    def sync_todoist_to_things(self, tag_import=False):

        """Sync todoist inbox todos into a given Things location.

         Args:
          tag_import: tag all imported todos with "todoist_sync"

        """

        tags = ["todoist_sync"] if tag_import else []

        todoist_todos = self.todoist.get_all_todos(self.todoist.get_inbox_id())

        for todoist_todo in todoist_todos:
            # This list will include subtasks - we cover those later
            # so ignore for now
            if todoist_todo.parent_id:
                continue

            name = todoist_todo.content
            todoist_id = str(todoist_todo.id)

            if todoist_id in self.state["todoist_to_things"]:
                LOG.debug("Todo %s (\"%s\") synced already", todoist_id, name)

                things_id = self.state["todoist_to_things"][todoist_id]
                if (todoist_todo.is_completed and
                        things_id not in self.closed_things_ids()):
                    # todo is complete, complete locally
                    if self.dry_run:
                        LOG.info("[dry-run] Would mark '%s' as complete in"
                                 " Things", name)
                    else:
                        self.things.set_complete(things_id)
                        LOG.info("Marked '%s' as complete", name)

                continue

            if not todoist_todo.is_completed:
                if self.dry_run:
                    LOG.info("[dry-run] Would create Things todo '%s' in %s",
                             name, self.things_location)
                    self._dry_run_todoist_parents.add(todoist_id)
                    continue

                things_id = self.things.create_todo(
                    name, self.things_location, tags=tags)
                if not things_id:
                    # The todo exists in Things but we have no ID to
                    # record for it. create_todo() has said as much.
                    continue

                self.state["todoist_to_things"][todoist_id] = things_id
                self.state["things_to_todoist"][things_id] = todoist_id

        self.sync_todoist_subtasks_to_things(todoist_todos)

        # TODO better return
        return self.state

    def sync_todoist_subtasks_to_things(self, todoist_todos):
        """
        Sync Todoist subtasks into Things todo checklists.

        We convert todoist subtasks (which are actually true tasks) to
        Things checklists (which are attributes of things tasks).
        * For now we support a single level of subtask-ery, and
           ignore subsubtasks.
        * We also can't sync resolved subtasks into checklists in Things
          due to limitations of the URL system. We could hack around this
          with applescript as in pythings but who wants to go back to
          doing that ;_;

         todoist_todos: todos from the Todoist inbox, as returned by
          ToDoistInterface.get_all_todos().

        """

        subtasks_by_parent = self.todoist.get_subtasks(todoist_todos)

        for parent_id, subtasks in subtasks_by_parent.items():
            things_parent = self.state["todoist_to_things"].get(parent_id)
            if not things_parent:
                if parent_id in self.state["todoist_to_things_subtasks"]:
                    LOG.warning(
                        "Todoist todos %s sit below a subtask, and a Things"
                        " checklist item cannot hold a checklist of its own,"
                        " so they have not been synced.",
                        ", ".join("'%s'" % task.content for task in subtasks))
                elif parent_id in self._dry_run_todoist_parents:
                    for subtask in subtasks:
                        if not subtask.is_completed:
                            LOG.info(
                                "[dry-run] Would add checklist item '%s' to a"
                                " Things todo in %s",
                                subtask.content, self.things_location)
                # Otherwise we haven't seen a parent at all
                continue

            closed_items = None

            for subtask in subtasks:
                name = subtask.content
                subtask_id = str(subtask.id)
                item_uuid = self.state["todoist_to_things_subtasks"].get(
                    subtask_id)

                if item_uuid:
                    if not subtask.is_completed:
                        continue

                    # Reading the checklist is only worth it once it is
                    # known that there is a completion to reflect.
                    if closed_items is None:
                        closed_items = self.closed_checklist_items(
                            things_parent)
                    if item_uuid not in closed_items:
                        self._unclosable_checklist_items.append(name)
                    continue

                if subtask.is_completed:
                    continue

                if self.dry_run:
                    LOG.info("[dry-run] Would add checklist item '%s' to a"
                             " Things todo in %s", name, self.things_location)
                    continue

                item_uuid = self.things.create_checklist_item(
                    things_parent, name)
                if not item_uuid:
                    # The item may well be on the todo, but there is no
                    # ID to record. create_checklist_item() has said so.
                    continue

                self.state["todoist_to_things_subtasks"][subtask_id] = \
                    item_uuid
                self.state["things_to_todoist_subtasks"][item_uuid] = \
                    subtask_id

        self._warn_about_unclosable_items()

    def closed_checklist_items(self, things_uuid):
        """Return the IDs of a Things todo's checklist items that are done."""

        return {item["uuid"]
                for item in self.things.get_checklist_items(things_uuid)
                if item["status"] in thingsinterface.CLOSED_CHECKLIST_STATUSES}

    def _warn_about_unclosable_items(self):
        """Report the completions that could not be mirrored in Things.

        Gathered up and reported in one go rather than one warning per
        item, as this is a standing state of affairs rather than a
        one-off: every run will find these again until they are ticked
        off in Things by hand.
        """

        if not self._unclosable_checklist_items:
            return

        LOG.warning(
            "Todoist subtask(s) %s are complete, but the Things URL scheme"
            " offers no way to tick off a single checklist item, so they have"
            " been left alone. Tick them off in Things to have the two match"
            " and to stop this warning.",
            ", ".join("'%s'" % name
                      for name in self._unclosable_checklist_items))
        self._unclosable_checklist_items = []


def read_state(statefile):
    """Load the sync state from disk, falling back to an empty state."""

    state = new_state()

    if not statefile or not os.path.isfile(statefile):
        return state

    with open(statefile, encoding="utf-8") as state_f:
        try:
            stored = json.load(state_f)
        except ValueError:
            sys.exit("Failed to read state file %s! Is it valid JSON?" %
                     statefile)

    state.update(stored)

    # An unrecognised version means the stored Todoist IDs were issued by
    # a different API generation. They would all miss, so every todo would
    # be silently re-imported - refuse rather than duplicate.
    if stored.get("version") != STATE_VERSION and has_mappings(state):
        sys.exit(
            "State file %s holds version %s mappings, but this toThingist"
            " expects version %d. Inconsistencies between versions make"
            " this likely to cause a huge mess at best. Move the file"
            " aside to start from an empty state." % (
                statefile, stored.get("version", "0 (pre-API-v1)"),
                STATE_VERSION)
        )

    state["version"] = STATE_VERSION
    return state


def write_state(statefile, state):
    """Write the sync state to disk, replacing it atomically."""

    # Avoid zeroing the file when the user's system has no disk space
    # by writing a tempfile alongside it and renaming over the original
    state_dir = os.path.dirname(statefile) or "."
    temp_state_f = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=state_dir, delete=False)
    try:
        with temp_state_f:
            json.dump(state, temp_state_f)
        os.replace(temp_state_f.name, statefile)
    except BaseException:
        os.unlink(temp_state_f.name)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", dest="verbose",
                        help="Be verbose",
                        action="store_true")
    parser.add_argument("-S", "--print-sql", dest="print_sql",
                        help="Print every SQL query run against the Things"
                             " database. Implies --verbose",
                        action="store_true")
    parser.add_argument("-n", "--dry-run", dest="dry_run",
                        help="Report what would be synced without changing"
                             " anything in Todoist, Things or the state file",
                        action="store_true")
    parser.add_argument("-c", "--config",
                        action="store", dest="configpath",
                        default="~/.tothingist",
                        help="alternate path for configuration file")

    options = parser.parse_args()

    verbose = options.verbose or options.print_sql

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s")

    config = configparser.ConfigParser()
    if not config.read(os.path.expanduser(options.configpath)):
        sys.exit("No configuration file found at %s" % options.configpath)

    try:
        api_key = config.get("login", "api_token")
        statefile = os.path.expanduser(config.get("config", "statefile"))
        things_location = config.get("config", "thingslocation")
    except (configparser.NoSectionError, configparser.NoOptionError) as exc:
        sys.exit(
            "Incomplete configuration in %s: %s" % (options.configpath, exc)
        )

    # Optional: things.py finds the database on its own unless told
    # otherwise (see also the THINGSDB environment variable).
    things_db = config.get("config", "thingsdb", fallback=None)

    todoist_obj = todoistinterface.ToDoistInterface(api_key)
    things_obj = thingsinterface.ThingsInterface(filepath=things_db,
                                                 print_sql=options.print_sql)

    tothingist_obj = ToThingist(todoist_obj, things_obj, things_location,
                                read_state(statefile),
                                dry_run=options.dry_run)

    tothingist_obj.sync_todoist_to_things(tag_import=True)
    tothingist_obj.sync_things_to_todoist()

    if options.dry_run:
        LOG.info("[dry-run] Not writing state file %s", statefile)
    elif statefile:
        if not has_mappings(tothingist_obj.state):
            LOG.warning("Not writing state file as there is no content"
                        " to sync. This could be in error or you'll need"
                        " to create at least one todo.")
        else:
            write_state(statefile, tothingist_obj.state)


if __name__ == "__main__":
    main()
