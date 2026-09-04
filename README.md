ToThingist
==========

Bidirectional sync between [Things](http://culturedcode.com/things/) and [ToDoist](https://todoist.com).

How to use ToThingist
---------
1. Install ToThingist via `pip` (or similar).
2. Create a Todoist API key [via the settings panel](https://app.todoist.com/app/settings/integrations/developer)
3. Copy the .tothingist config to your home directory. Add your API token to `api_token`. Specify the projects you'd like to sync.
4. Run ToThingist.py, the requested projects will sync. Todos will be created and resolved as needed. If needed, Things will prompt to add enable the URL handler.

For best results, run ToThingist via cron or similar scheduled tooling.

What is supported
----------
ToThingist will sync the state of Todos between Things and Todoist. This includes:
- Creating Todos missing on either end, on any number of projects
- Resolving or cancelling todos
- Syncing sub-todos/checklists within TODOs
- Accesing todos via Areas in Things

ToThingist can sync shared projects in Todoist, accidentally adding the ability to share todos with other users to Things.

What is not supported
---------
- Resolving checklist items in Things (see below)
- Syncing changed content like textboxes in existing Todos (will be implemented in future)

Requirements
---------
ToThingist requires Python 3 and macOS with Things installed.
Dependencies are listed in ```pyproject.toml```.

ToThingist is built around the assumption that it runs on the computer
that Things is installed upon. Running ToThingist on other platforms
is not supported and it cannot remotely alter todos.

Operation
---------

ToThingist.py reads a config file at ```.tothingist```. This file
specifies a few obvious things like the Todoist API token. The example
```.tothingist``` file is a complete example.

The ```statefile``` option is currently used to maintain a mapping
between todoist IDs and things IDs. Use of the state file is not
required but not using it will result in duplication of imported todos
so you should probably use it.

Writing to Things requires the use of the Things URL scheme. See the
[official Things
documentation](https://culturedcode.com/things/support/articles/2803573/#overview-authorization)
for details on how to enable its use.

The ```[projects]``` section configures which Todoist project syncs with
which Things location, one pair per line:

```
[projects]
Inbox: Today
Work: Work
Household/Renovation: Home/Renovation
```

On the left is a Todoist project name, where ```Inbox``` always means
the Todoist inbox whatever the account's language calls it. On the
right is one of the built-in Things lists (```Inbox```, ```Today```,
```Anytime```, ```Someday```) or the title of a project or area. Don't
use ```Upcoming``` as this can't be written to in normal situations.

Names are matched exactly, case included. Both applications allow two
projects to share a name, so where a name is ambiguous a Todoist
project can be named by its ```Parent/Child``` path and a Things
project by its ```Area/Project``` one. ToThingist says which paths it
found rather than guessing between them.

Two Todoist projects can't be mapped to the same Things location.
Things can get a little unpredictable if a project is synced that
contains todos that are also in Today for example, as a todo will only
get synced once but the relations will be maintained.

For backwards compatibility, a config file with no ```[projects]```
section falls back to the ```thingslocation``` option, which is the
same as mapping ```Inbox``` to that location. When ```[projects]``` is
present, ```thingslocation``` is ignored.

The optional ```thingsdb``` option points at the Things database. This
isn't generally required as thingsapi can find it in most cases. The
`THINGSDB` environment variable can also be set.

Running ToThingist.py will sync all todos in the Things location to
the Todoist project and all todos in the Todoist project to the Things
location, for every configured project. The mapping between todos is
maintained in the state file.

The script is largely designed with an eye to it being run via cron.

Current state
---------

ToThingist can currently sync any number of Todoist projects to
matching Things lists, projects or areas, and back again. The
completed/cancelled attributes of both systems are synced back and
forth, wherever a todo was synced to. Changes to things like text will
not be synced between existing todos.

Subtasks (single-level subtasks in todoist and task checklists within
Things) are synced in both directions. Things checklist tasks cannot
currently be checked off programmatically and so this must be done
manually when a Todoist subtask is complete. Things checklist items
that are checked off will resolve Todoist subtasks. See
[Subtasks](#subtasks) below for more details on how subtasks are
handled.

ToThingist talks to Todoist API v1 via the official
[todoist-api-python](https://github.com/Doist/todoist-api-python)
SDK. Due to constraints in the ToDoist API, *completed todos older
than 90 days will not be completed in Things*. This is a limitation of
the API and not something that can be addressed programatically, so
ensure that your crons run regularly. Things is read with
[things.py](https://github.com/thingsapi/things.py), which reads the
Things database directly; the same 90 day window is applied to it for
symmetry.

All reads are done via `things.py`. Writes are managed via the [Things
URL scheme](https://culturedcode.com/things/help/url-scheme/)
so Things must be installed on the machine running ToThingist. Two
things follow from that:

* The URL scheme doesn't tell us the ID of a todo it creates, so we
  have to do a second read to get the ID. This means that in some rare
  cases, the ID won't be found and the task will be skipped in the
  sync - next sync will pick this todo up. Deleting unsynced TODOs
  might create weird behaviour in these cases.
* If the ```todoist_sync``` tag doesn't exist already, it can't be
  applied by ToThingist. ToThingist will warn when a tag is
  going to be dropped.

Subtasks
---------

Todoist subtasks are ordinary todos that hang off another todo. Things
checklists look and function the same to the user but are conceptually
quite different, functioning as attributes of a task rather than a
relation between task types.

New subtasks are synced in both directions, attached to their parent
tasks. Their IDs are kept in the state file in maps of their own,
separate from the todo maps, so migration between state file formats
is not required to start using them.

*Things checklist items cannot be marked as done programmatically at
present.* ToThingist warns once per run listing the subtasks this
applies to, and will keep doing so until they are marked done in
Things manually. Todoist subtasks that map to Things checklist items
will be resolved as expected.

Two smaller limits, both from the same URL scheme:

* ToThingist supports one level of nesting for tasks - subtasks of
  subtasks will not be synced.
* Things checklists cannot be longer than 100 elements.

ToThingist is not backwards compatible with older versions that used
the older todoist API which no longer works. Having an old state file
could result in the recreation of old todos or other messes.
ToThingist will panic on a mismatched state file.

Pass ```--dry-run``` (```-n```) to see what a sync would do without
touching Todoist, Things or the state file.

Pass ```--print-sql``` (```-S```) to print every SQL query things.py
runs against the Things database, which is handy when a location or a
todo isn't showing up where you expect.
