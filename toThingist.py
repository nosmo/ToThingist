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
    """Return an empty sync state mapping."""

    return {"version": STATE_VERSION,
            "todoist_to_things": {}, "things_to_todoist": {}}


def has_mappings(state):
    """Return True if the state holds at least one todo mapping."""

    return bool(state["todoist_to_things"] or state["things_to_todoist"])


class ToThingist(object):

    def __init__(self, todoist_obj, things_obj, things_location, state,
                 dry_run=False):
        self.todoist = todoist_obj
        self.things = things_obj
        self.things_location = things_location
        self.state = state
        self.dry_run = dry_run
        self._closed_things_ids = None

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

        for todo in self.things.get_todos(self.things_location):
            if todo["uuid"] in self.state["things_to_todoist"]:
                LOG.debug("Todo %s (\"%s\") synced already",
                          todo["uuid"], todo["title"])
                continue

            if self.dry_run:
                LOG.info("[dry-run] Would create ToDoist todo '%s' in the"
                         " inbox", todo["title"])
                continue

            new_todo = self.todoist.create_todo(todo["title"], inbox_id)
            todoist_id = str(new_todo.id)
            self.state["todoist_to_things"][todoist_id] = todo["uuid"]
            self.state["things_to_todoist"][todo["uuid"]] = todoist_id

        #TODO better return
        return self.state


    def sync_todoist_to_things(self, tag_import=False):

        """Sync todoist inbox todos into a given Things location.

         Args:
          tag_import: tag all imported todos with "todoist_sync"

        """

        tags = ["todoist_sync"] if tag_import else []

        for todoist_todo in self.todoist.get_all_todos(
                self.todoist.get_inbox_id()):
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
                    continue

                things_id = self.things.create_todo(
                    name, self.things_location, tags=tags)
                if not things_id:
                    # The todo exists in Things but we have no ID to
                    # record for it. create_todo() has said as much.
                    continue

                self.state["todoist_to_things"][todoist_id] = things_id
                self.state["things_to_todoist"][things_id] = todoist_id
        # TODO better return
        return self.state


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
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        sys.exit("Incomplete configuration in %s: %s" % (options.configpath, e))

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
