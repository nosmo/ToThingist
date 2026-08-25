#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Read Things db with things.py, write to Things via URL scheme

things.py allows us read-only access to the Things sqlite
file. Things itself allows us programmatic writes using the things:/// URL.

See https://culturedcode.com/things/help/url-scheme/ for the URL
scheme.

"""

import datetime
import logging
import os.path
import subprocess
import time
import urllib.parse

import things

LOG = logging.getLogger("tothingist")

# How far back to look for todo completion/cancellation. Used for
# consistency with todoist limits
CLOSED_WINDOW_DAYS = 90

# Polling limits for querying the database after creation of a new
# TODO, required in order to get the ID of the new TODO
CREATE_POLL_ATTEMPTS = 40
CREATE_POLL_INTERVAL = 0.25

# Max checklist items in Things. Anything over this is dropped
CHECKLIST_LIMIT = 100

BUILTIN_READERS = {
    "inbox": things.inbox,
    "today": things.today,
    "anytime": things.anytime,
    "someday": things.someday,
    "upcoming": things.upcoming,
}

# Lists we can't sync to
RO_LISTS = ("logbook", "trash")

# Statuses that things.py reports for closed checklist items
CLOSED_CHECKLIST_STATUSES = ("completed", "canceled")

# The URL scheme "when" value that files a new todo into each built-in
# list. The Inbox is where a todo lands when no "when" is given at all,
# and "upcoming" is only reachable by naming a specific date, so neither
# has a value here.
BUILTIN_WHEN = {
    "inbox": None,
    "today": "today",
    "anytime": "anytime",
    "someday": "someday",
}


class ThingsInterface:
    """Wrapper for the bits of Things that toThingist needs."""

    def __init__(self, filepath=None, print_sql=False):
        """
         filepath: path to the Things SQLite database. If None, things.py
          picks it up from $THINGSDB or the default install location.
         print_sql: print every SQL query things.py runs.
        """

        self.filepath = os.path.expanduser(filepath) if filepath else None
        self.print_sql = print_sql
        self._locations = {}

    def _query_args(self):
        """Return the things.py arguments shared by every query.

        things.py opens the database per call, so these have to be
        passed to each one rather than set up once.
        """

        args = {"print_sql": self.print_sql}
        if self.filepath:
            args["filepath"] = self.filepath
        return args

    def resolve_location(self, location):
        """
        Work out what a configured Things location refers to.

        Returns a ("builtin", name), ("project", project dict) or
        ("area", area dict) tuple. Raises KeyError if Things has no
        such location.

         location: the name of a built-in list ("Inbox", "Today",
          "Anytime", "Someday", "Upcoming"), or the title of a project
          or area.
        """

        if location in self._locations:
            return self._locations[location]

        name = location.strip().lower()
        if name in BUILTIN_READERS:
            resolved = ("builtin", name)
        elif name in RO_LISTS:
            raise ValueError(
                "%r only holds todos that are already done with, so there is"
                " nothing to sync there" % location)
        else:
            for project in things.projects(**self._query_args()):
                if project["title"] == location:
                    resolved = ("project", project)
                    break
            else:
                for area in things.areas(**self._query_args()):
                    if area["title"] == location:
                        resolved = ("area", area)
                        break
                else:
                    raise KeyError(
                        "No Things location called %r (expected one of %s, or"
                        " the title of a project or area)" % (
                            location, ", ".join(sorted(BUILTIN_READERS))))

        self._locations[location] = resolved
        return resolved

    def get_todos(self, location):
        """
        Get the todos waiting to be done in a Things location.

        Completed and cancelled todos are not included - see
        get_closed_todos() for those.

         location: a location as accepted by resolve_location(). Todos
          filed directly in an area are returned, but not those in the
          area's projects.
        """

        kind, resolved = self.resolve_location(location)

        if kind == "builtin":
            return BUILTIN_READERS[resolved](type="to-do",
                                             **self._query_args())

        if kind == "project":
            return things.todos(project=resolved["uuid"],
                                **self._query_args())

        return things.todos(area=resolved["uuid"], **self._query_args())

    def get_closed_todos(self, days=CLOSED_WINDOW_DAYS):
        """
        Get todos completed or cancelled in the last `days` days.

        Closing a todo takes it out of whichever list it was in, so
        these are looked up by their stop date rather than by location.
        """

        cutoff = datetime.date.today() - datetime.timedelta(days=days)

        # Only completed and cancelled todos have a stop date, so this
        # needs no status filter of its own.
        return things.todos(status=None, stop_date=">=%s" % cutoff.isoformat(),
                            **self._query_args())

    def create_todo(self, name, location, tags=(), notes=""):
        """
        Create a todo and return its Things ID, or None if it could
        not be found again after creation.

         name: the title of the todo.
         location: the list, project or area to create the todo in.
          See resolve_location() for the accepted values.
         tags: tags to apply. Things ignores tags that don't already
          exist, so these have to have been created in the app first.
         notes: the note body of the todo.
        """

        if not name:
            raise ValueError("Refusing to create a Things todo with no title")

        parameters = {"title": name}

        kind, resolved = self.resolve_location(location)
        if kind == "builtin":
            if resolved not in BUILTIN_WHEN:
                raise ValueError(
                    "Things todos cannot be created in %r - a todo can only"
                    " end up there by being scheduled for a specific date" %
                    location)
            # No "when" at all is what puts a todo in the Inbox.
            if BUILTIN_WHEN[resolved]:
                parameters["when"] = BUILTIN_WHEN[resolved]
        else:
            # The URL scheme takes projects and areas alike as a list
            # title, so the resolved location is only used to check
            # that there is something there to create a todo in.
            parameters["list"] = resolved["title"]

        if tags:
            self._warn_about_missing_tags(tags)
            parameters["tags"] = ",".join(tags)
        if notes:
            parameters["notes"] = notes

        # Any todo of this name that exists already is not the one we
        # are about to create, however identical it looks.
        existing = {todo["uuid"] for todo in self._get_todos_titled(name)}

        self._open_url(things.url(command="add", **parameters))

        for _ in range(CREATE_POLL_ATTEMPTS):
            time.sleep(CREATE_POLL_INTERVAL)
            created = [todo for todo in self._get_todos_titled(name)
                       if todo["uuid"] not in existing]
            if created:
                # More than one match means something else created a
                # todo of the same name at the same moment. Newest wins.
                return max(created, key=lambda todo: todo["created"])["uuid"]

        LOG.warning(
            "Created Things todo '%s' but it had not appeared in the Things"
            " database after %.1f seconds, so it cannot be recorded in the"
            " state file. It will be created again on the next run unless"
            " you delete one of the two copies.",
            name, CREATE_POLL_ATTEMPTS * CREATE_POLL_INTERVAL)
        return None

    def set_complete(self, uuid):
        """
        Set a todo's state to complete.

         uuid: the Things ID of the todo.
        """

        self._open_url(self._update_url(uuid, completed="true"))

    def get_checklist_items(self, uuid):
        """
        Get checklist items of a todo. All items will be returned,
        open, cancelled or completed.

         uuid: the Things ID of the todo the checklist hangs off.

        """

        return things.checklist_items(uuid, **self._query_args())

    def create_checklist_item(self, uuid, name):
        """
        Append an item to a todo's checklist and return the item's
        Things ID. Returns None if the item couldn't be found or if a
        checklist is over 100 items.

         uuid: the Things ID of the todo to append to.
         name: the title of the checklist item.
        """

        if not name:
            raise ValueError(
                "Refusing to create a Things checklist item with no title"
            )

        # The URL scheme separates checklist items by newline, so a
        # title containing one would silently arrive as several items -
        # and only one of them could be recorded in the state file.
        title = " ".join(name.splitlines())
        if title != name:
            LOG.warning(
                "Checklist item '%s' spans several lines. Things reads a"
                " line break as the start of another item, so it has been"
                " flattened to '%s'.", name, title)

        existing = {item["uuid"] for item in self.get_checklist_items(uuid)}

        self._open_url(self._update_url(
            uuid, **{"append-checklist-items": title}))

        for _ in range(CREATE_POLL_ATTEMPTS):
            time.sleep(CREATE_POLL_INTERVAL)
            created = [item for item in self.get_checklist_items(uuid)
                       if item["uuid"] not in existing
                       and item["title"] == title]
            if created:
                # As in create_todo(), a second match means something
                # else added an identical item just now. Newest wins.
                return max(created, key=lambda item: item["created"])["uuid"]

        LOG.warning(
            "Asked Things to add checklist item '%s', but it had not appeared"
            " on todo %s after %.1f seconds, so it cannot be recorded in the"
            " state file. Either the todo is at its %d item limit, or the"
            " item will be added again on the next run unless you delete it.",
            title, uuid, CREATE_POLL_ATTEMPTS * CREATE_POLL_INTERVAL,
            CHECKLIST_LIMIT)
        return None

    def _get_todos_titled(self, title):
        """Get every todo, closed ones included, with exactly this title."""

        # search_query matches anywhere in a title or note, so the
        # results still have to be narrowed down to an exact title.
        return [todo
                for todo in things.todos(search_query=title, status=None,
                                         **self._query_args())
                if todo["title"] == title]

    def _warn_about_missing_tags(self, tags):
        """Warn about tags that Things will silently drop."""

        known = {tag["title"] for tag in things.tags(**self._query_args())}
        missing = [tag for tag in tags if tag not in known]
        if missing:
            LOG.warning(
                "Tag(s) %s do not exist in Things and will be dropped from"
                " todos created by this run. Create them in Things to have"
                " them applied.", ", ".join(missing))

    def _update_url(self, uuid, **parameters):
        """
        Build a things:///update URL.

        things.url() builds these too, but it reads the authentication
        token that updates require from the default database rather than
        from the one we were told to use.
        """

        auth_token = things.token(**self._query_args())
        if not auth_token:
            raise ValueError(
                "Things URL scheme authentication token could not be read")

        query = urllib.parse.urlencode(
            {"id": uuid, **parameters, "auth-token": auth_token},
            quote_via=urllib.parse.quote)
        return "things:///update?%s" % query

    @staticmethod
    def _open_url(url):
        """Hand a things:/// URL to the Things app."""

        LOG.debug("Opening %s", url)

        # -g leaves Things in the background, which matters when this is
        # running from cron and the user is doing something else.
        try:
            subprocess.run(["open", "-g", url], check=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Could not run open(1) to talk to Things - toThingist needs"
                " to run on the Mac that Things is installed on") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Things refused the URL %s (open exited %d)" % (
                    url, exc.returncode)) from exc


def main():
    import argparse
    import pprint

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("location", nargs="?", default="Today",
                        help="Things location to dump")
    parser.add_argument("--print-sql", dest="print_sql", action="store_true",
                        help="print the SQL queries used")
    options = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    interface = ThingsInterface(print_sql=options.print_sql)
    pprint.pprint(interface.get_todos(options.location))


if __name__ == "__main__":
    main()
