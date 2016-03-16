"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

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
        env_json = self.build_json['spec']['strategy']['customStrategy']['env']
        try:
            p = [env for env in env_json if env["name"] == "ATOMIC_REACTOR_PLUGINS"]
        except TypeError:
            raise RuntimeError("\"env\" is not iterable")
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key ATOMIC_REACTOR_PLUGINS")
        dock_json_str = p[0]['value']
        dock_json = json.loads(dock_json_str)
        return dock_json

    def dock_json_get_plugin_conf(self, plugin_type, plugin_name):
        """
        Return the configuration for a plugin.

        Raises KeyError if there are no plugins of that type.
        Raises IndexError if the named plugin is not listed.
        """
        match = [x for x in self.dock_json[plugin_type] if x.get('name', None) == plugin_name]
        return match[0]

    def remove_plugin(self, plugin_type, plugin_name):
        """
        if config contains plugin, remove it
        """
        for p in self.dock_json[plugin_type]:
            if p.get('name', None) == plugin_name:
                self.dock_json[plugin_type].remove(p)
                break

    def dock_json_has_plugin_conf(self, plugin_type, plugin_name):
        """
        Check whether a plugin is configured.
        """

        try:
            self.dock_json_get_plugin_conf(plugin_type, plugin_name)
            return True
        except (KeyError, IndexError):
            return False

    def _dock_json_get_plugin_conf_or_fail(self, plugin_type, plugin_name):
        try:
            conf = self.dock_json_get_plugin_conf(plugin_type, plugin_name)
        except KeyError:
            raise RuntimeError("Invalid dock json: plugin type '%s' misses" % plugin_type)
        except IndexError:
            raise RuntimeError("no such plugin in dock json: \"%s\"" % plugin_name)
        return conf

    def dock_json_set_arg(self, plugin_type, plugin_name, arg_key, arg_value):
        plugin_conf = self._dock_json_get_plugin_conf_or_fail(plugin_type, plugin_name)
        plugin_conf.setdefault("args", {})
        plugin_conf['args'][arg_key] = arg_value

    def dock_json_merge_arg(self, plugin_type, plugin_name, arg_key, arg_dict):
        plugin_conf = self._dock_json_get_plugin_conf_or_fail(plugin_type, plugin_name)

        # Values supplied by the caller override those from the template JSON
        template_value = plugin_conf['args'].get(arg_key, {})
        if not isinstance(template_value, dict):
            template_value = {}

        value = copy.deepcopy(template_value)
        value.update(arg_dict)
        plugin_conf['args'][arg_key] = value

    def write_dock_json(self):
        env_json = self.build_json['spec']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "ATOMIC_REACTOR_PLUGINS"]
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key ATOMIC_REACTOR_PLUGINS")
        p[0]['value'] = json.dumps(self.dock_json)
