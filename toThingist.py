#!/usr/bin/python3

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


def new_state():
    """Return an empty sync state mapping."""

    return {"todoist_to_things": {}, "things_to_todoist": {}}


class ToThingist(object):

    def __init__(self, todoist_obj, things_location, state):
        self.todoist = todoist_obj
        self.things_location = things_location
        self.state = state

    def sync_things_to_todoist(self):
        """
        Sync the Things location to the ToDoist inbox.

        """

        inbox_id = self.todoist.get_inbox_id()

        for todo in thingsinterface.ToDos(self.things_location):
            if todo.thingsid in self.state["things_to_todoist"]:
                todoist_id = self.state["things_to_todoist"][todo.thingsid]
                if todo.is_closed() or todo.is_cancelled():
                    self.todoist.set_complete(todoist_id)
                    LOG.info("Marking task '%s' as complete in ToDoist",
                             todo.name)

                LOG.debug("Todo %s (\"%s\") synced already",
                          todo.thingsid, todo.name)
                continue

            new_todo = self.todoist.create_todo(todo.name, inbox_id)
            todoist_id = str(new_todo["id"])
            self.state["todoist_to_things"][todoist_id] = todo.thingsid
            self.state["things_to_todoist"][todo.thingsid] = todoist_id

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
            name = todoist_todo["content"]
            todoist_id = str(todoist_todo["id"])

            if todoist_id in self.state["todoist_to_things"]:
                LOG.debug("Todo %s (\"%s\") synced already", todoist_id, name)

                if todoist_todo["checked"]:
                    # todo is checked off - check off locally
                    to_complete = thingsinterface.ToDo._getTodoByID(
                        self.state["todoist_to_things"][todoist_id])
                    to_complete.complete()
                    LOG.info("Marked '%s' as complete", name)

                continue

            if not todoist_todo["checked"]:
                newtodo = thingsinterface.ToDo(name=name,
                                               tags=tags,
                                               location=self.things_location)
                self.state[
                    "todoist_to_things"][todoist_id] = newtodo.thingsid
                self.state[
                    "things_to_todoist"][newtodo.thingsid] = todoist_id
        # TODO better return
        return self.state


def read_state(statefile):
    """Load the sync state from disk, falling back to an empty state."""

    state = new_state()

    if not statefile or not os.path.isfile(statefile):
        return state

    with open(statefile, encoding="utf-8") as state_f:
        try:
            state.update(json.load(state_f))
        except ValueError:
            sys.exit("Failed to read state file %s! Is it valid JSON?" %
                     statefile)

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
    parser.add_argument("-c", "--config",
                        action="store", dest="configpath",
                        default="~/.tothingist",
                        help="alternate path for configuration file")

    options = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if options.verbose else logging.INFO,
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

    todoist_obj = todoistinterface.ToDoistInterface(api_key)

    tothingist_obj = ToThingist(todoist_obj, things_location,
                                read_state(statefile))

    tothingist_obj.sync_todoist_to_things(tag_import=True)
    tothingist_obj.sync_things_to_todoist()

    if statefile:
        if not any(tothingist_obj.state.values()):
            LOG.warning("Not writing state file as there is no content"
                        " to sync. This could be in error or you'll need"
                        " to create at least one todo.")
        else:
            write_state(statefile, tothingist_obj.state)

if __name__ == "__main__":
    main()
