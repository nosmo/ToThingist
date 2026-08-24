toThingist
==========

Bidirectional sync between [Things](http://culturedcode.com/things/) and [ToDoist](https://todoist.com).

Requirements
---------
toThingist requires Python 3 and the
[pythings](https://github.com/nosmo/pythings) module. Remaining
dependencies are listed in ```requirements.txt```.

Operation
---------

toThingist.py reads a config file at ```.tothingist```. This file
specifies a few obvious things like the Todoist API token. The example
```.tothingist``` file is a complete example.

The ```statefile``` option is currently used to maintain a mapping
between todoist IDs and things IDs. Use of the state file is not
required but not using it will result in duplication of imported todos
so you should probably use it.

The ```thingslocation``` option is used to indicate where a todo
should go once imported from todoist. (ie "Today", "Inbox" etc)

Running toThingist.py will sync all todos in ```thingslocation``` to
the inbox of the configured Todoist account, and all todos from the
Todoist inbox to ```thingslocation```. The mapping between todos is
maintained in the state file.

The script is largely designed with an eye to it being run via cron.

Current state
---------

The script can currently sync the Todoist inbox to a list in Things,
and a Things list to the Todoist inbox. The completed/cancelled
attributes of both systems are synced back and forth. Changes to
the content of existing todo objects are not mirrored.
