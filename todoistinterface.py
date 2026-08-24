#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import datetime
import os.path

from todoist_api_python.api import TodoistAPI

# Fetch this many days of completed tasks - 90 is current max :/
COMPLETED_WINDOW_DAYS = 90

class ToDoistInterface(object):
    """Ugly wrapper for the ToDoist.com API"""

    def __init__(self, token):
        self.token = token
        self.api = TodoistAPI(token)

    def get_projects(self):
        '''
        Get all projects.
        '''
        return [project
                for page in self.api.get_projects()
                for project in page]

    def get_uncompleted_todos(self, project_id):
        '''
        Get all uncompleted todo items in a project.
        '''
        return [task
                for page in self.api.get_tasks(project_id=project_id)
                for task in page]

    def get_completed_todos(self, project_id):
        '''
        Get as many completed todo items in a project as possible.

        API limitations mean that we cannot get all completed items
        '''
        until = datetime.datetime.now(datetime.timezone.utc)
        since = until - datetime.timedelta(days=COMPLETED_WINDOW_DAYS)

        # This endpoint has no project_id parameter, so filter locally.
        return [task
                for page in self.api.get_completed_tasks_by_completion_date(
                    since=since, until=until)
                for task in page
                if task.project_id == project_id]

    def get_all_todos(self, project_id):
        '''
        Get all todo objects in a project.

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

    def create_todo(self, name, project_id):
        '''
        Create a new todo.

         name: the label for the todo item.
         project_id: the id of the project in which to create a todo.
        '''

        return self.api.add_task(name, project_id=project_id)

    def get_inbox_id(self):
        '''
        Get the project ID for the Inbox project.
        '''
        for project in self.get_projects():
            if project.is_inbox_project:
                return project.id
        raise LookupError("No Inbox project found in this Todoist account")

def main():
    config = configparser.ConfigParser()
    config.read(os.path.expanduser("~/.tothingist"))
    api_key = config.get('login', 'api_token')
    a = ToDoistInterface(api_key)
    inbox_id = a.get_inbox_id()
    import pprint
    pprint.pprint(a.get_all_todos(inbox_id))

if __name__ == "__main__":
    main()
