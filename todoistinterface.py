#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import os.path

import todoist

class ToDoistInterface(object):
    """Ugly wrapper for the ToDoist.com API"""

    def __init__(self, token):
        self.token = token
        self.api = todoist.TodoistAPI(token)
        self.api.sync()

    def get_projects(self):
        '''
        Get all projects.
        '''
        return self.api.projects.all()

    def get_all_todos(self, project_id):
        '''
        Get all todo objects in a project.
        '''
        return [i for i in self.api.items.all()
                if i["project_id"] == project_id]

    def get_uncompleted_todos(self, project_id):
        '''
        Get all uncompleted todo items in a project.
        '''
        return [i for i in self.get_all_todos(project_id) if not i["checked"]]

    def get_completed_todos(self, project_id):
        '''
        Get all completed todo items in a project.
        '''
        return [i for i in self.get_all_todos(project_id) if i["checked"]]

    def set_complete(self, item_id):
        '''
        Set a todo's state to complete.

         item_id: the todoist ID of the todo.
        '''
        self.api.items.complete([item_id])
        self.api.commit()
        return True

    def create_todo(self, name, project_id):
        '''
        Create a new todo.

         name: the label for the todo item.
         project_id: the id of the project in which to create a todo.
        '''

        add_res = self.api.items.add(name, project_id)
        self.api.commit()
        return add_res

    def get_inbox_id(self):
        '''
        Get the project ID for the Inbox project.
        '''
        return [i for i in self.get_projects() if i["name"] == "Inbox"][0]["id"]

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
