"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import json
import copy


class DockJsonManipulator(object):
    """ """
    def __init__(self, build_json, dock_json):
        """ """
        self.build_json = build_json
        self.dock_json = dock_json

    def get_dock_json(self):
        """ return dock json from existing build json """
        env_json = self.build_json['parameters']['strategy']['customStrategy']['env']
        try:
            p = [env for env in env_json if env["name"] == "DOCK_PLUGINS"]
        except TypeError:
            raise RuntimeError("\"env\" is not iterable")
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key DOCK_PLUGINS")
        dock_json_str = p[0]['value']
        dock_json = json.loads(dock_json_str)
        return dock_json

    def _dock_json_get_plugin_conf(self, plugin_type, plugin_name):
        try:
            match = [x for x in self.dock_json[plugin_type] if x.get('name', None) == plugin_name]
        except KeyError:
            raise RuntimeError("Invalid dock json: plugin type '%s' misses" % plugin_type)
        if len(match) <= 0:
            raise RuntimeError("no such plugin in dock json: \"%s\"" % plugin_name)
        return match[0]

    def dock_json_set_arg(self, plugin_type, plugin_name, arg_key, arg_value):
        plugin_conf = self._dock_json_get_plugin_conf(plugin_type, plugin_name)
        plugin_conf['args'][arg_key] = arg_value

    def dock_json_merge_arg(self, plugin_type, plugin_name, arg_key, arg_dict):
        plugin_conf = self._dock_json_get_plugin_conf(plugin_type, plugin_name)

        # Values supplied by the caller override those from the template JSON
        template_value = plugin_conf['args'].get(arg_key, {})
        if not isinstance(template_value, dict):
            template_value = {}

        value = copy.deepcopy(template_value)
        value.update(arg_dict)
        plugin_conf['args'][arg_key] = value

    def write_dock_json(self):
        env_json = self.build_json['parameters']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "DOCK_PLUGINS"]
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key DOCK_PLUGINS")
        p[0]['value'] = json.dumps(self.dock_json)

