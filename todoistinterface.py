#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import datetime
import os.path

from todoist_api_python.api import TodoistAPI

# Fetch this many days of completed tasks - 90 is current max :/
COMPLETED_WINDOW_DAYS = 90

# Projects with identical names in different areas use this divider
PROJECT_PATH_SEPARATOR = "/"

INBOX_NAME = "Inbox"


class ToDoistInterface:
    """Ugly wrapper for the ToDoist.com API"""

    def __init__(self, token):
        self.token = token
        self.api = TodoistAPI(token)
        self._projects = None

    def get_projects(self, refresh=False):
        '''
        Get all projects and store the result.

         refresh: fetch the projects again rather than reusing the ones
          fetched earlier.
        '''
        if self._projects is None or refresh:
            self._projects = [project
                              for page in self.api.get_projects()
                              for project in page]
        return self._projects

    def get_project_path(self, project):
        '''
        Get the "Parent/Child" path of a project.

         project: a project as returned by get_projects().
        '''
        by_id = {each.id: each for each in self.get_projects()}

        names = [project.name]
        # Projects can't really be their own ancestor, but avoid
        # endless loops just in case
        seen = {project.id}
        parent_id = project.parent_id
        while parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            names.append(by_id[parent_id].name)
            parent_id = by_id[parent_id].parent_id

        return PROJECT_PATH_SEPARATOR.join(reversed(names))

    def resolve_project_id(self, name):
        '''
        Get the ID of the project called `name`.

        Raises LookupError if there is no such project, or if the name
        belongs to more than one of them.

         name: the name of a project, its "Parent/Child" path if the
          name alone is ambiguous, or "Inbox" for the inbox project
        '''
        wanted = name.strip()
        projects = self.get_projects()

        matches = [project for project in projects
                   if self.get_project_path(project) == wanted]
        if not matches:
            matches = [project for project in projects
                       if project.name.strip() == wanted]
        if not matches and wanted.lower() == INBOX_NAME.lower():
            # The inbox is the one project whose name is not its own -
            # it is named for the language the account is set up in.
            return self.get_inbox_id()

        if len(matches) == 1:
            return matches[0].id

        if not matches:
            raise LookupError(
                "No Todoist project called %r (this account has %s)" % (
                    name, ", ".join(sorted(self.get_project_path(project)
                                           for project in projects))))

        raise LookupError(
            "%d Todoist projects are called %r - name one of %s instead" % (
                len(matches), name,
                ", ".join(sorted(self.get_project_path(project)
                                 for project in matches))))

    def get_uncompleted_todos(self, project_id):
        '''
        Get all uncompleted todo items in a project.

        Subtasks are todos like any other, so they are included here.
        See also get_subtasks().
        '''
        return [task
                for page in self.api.get_tasks(project_id=project_id)
                for task in page]

    def get_completed_todos(self, project_id=None):
        '''
        Get as many completed todo items as possible.

        API limitations mean that we cannot get all completed items

         project_id: only return the completed todos of this project.
          The endpoint has no project parameter, so asking for one
          project costs the same as asking for all of them - callers
          syncing several projects want to filter one fetch themselves.
        '''
        until = datetime.datetime.now(datetime.timezone.utc)
        since = until - datetime.timedelta(days=COMPLETED_WINDOW_DAYS)

        # This endpoint has no project_id parameter, so filter locally.
        return [task
                for page in self.api.get_completed_tasks_by_completion_date(
                    since=since, until=until)
                for task in page
                if project_id is None or task.project_id == project_id]

    def get_all_todos(self, project_id):
        '''
        Get all todo objects in a project, subtasks included.

        Active and completed todos live behind separate endpoints, so
        this necessarily costs two sets of requests rather than one.
        '''
        return (self.get_uncompleted_todos(project_id) +
                self.get_completed_todos(project_id))

    def set_complete(self, item_id):
        '''
        Set a todo's state to complete.

         item_id: the todoist ID of the todo.
        '''
        return self.api.complete_task(item_id)

    def create_todo(self, name, project_id=None, parent_id=None):
        '''
        Create a new todo.

         name: the label for the todo item.
         project_id: the id of the project in which to create a todo.
         parent_id: the id of the todo to make this task a subtask of
          A subtask lives in its parent's project, so not rqeuired here.
        '''

        if parent_id:
            return self.api.add_task(name, parent_id=parent_id)

        if not project_id:
            raise ValueError(
                "A todoist todo must have either a project or parent"
            )

        return self.api.add_task(name, project_id=project_id)

    @staticmethod
    def get_subtasks(todos):
        '''
        Group todos by their parent ID

        Subtasks appear alongside all other todos, this
        function filters them out.

         todos: todo objects as returned by get_all_todos().
        '''

        subtasks = {}
        for todo in todos:
            if todo.parent_id:
                subtasks.setdefault(str(todo.parent_id), []).append(todo)
        return subtasks

    def get_inbox_id(self):
        '''
        Get the project ID for the Inbox project.
        '''
        for project in self.get_projects():
            if project.is_inbox_project:
                return project.id
        raise LookupError("No Inbox project found in this Todoist account")


def main():
    import argparse
    import pprint

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=INBOX_NAME,
                        help="Todoist project to dump")
    options = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(os.path.expanduser("~/.tothingist"))
    api_key = config.get('login', 'api_token')
    api = ToDoistInterface(api_key)
    pprint.pprint(api.get_all_todos(api.resolve_project_id(options.project)))


if __name__ == "__main__":
    main()
