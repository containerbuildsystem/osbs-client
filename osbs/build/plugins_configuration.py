"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import os
import json
import re

from osbs.constants import BUILD_TYPE_ORCHESTRATOR
from osbs.exceptions import OsbsException
from osbs import utils

logger = logging.getLogger(__name__)


class PluginsTemplate(object):
    def __init__(self, build_json_dir, template_path, customize_conf_path):
        self._template = None
        self._customize_conf = None
        self._build_json_dir = build_json_dir
        self._template_path = template_path
        self._customize_conf_path = customize_conf_path

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self._build_json_dir, self._template_path)
            logger.debug("loading template from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._template = json.load(fp)
            except (IOError, OSError) as ex:
                raise OsbsException("Can't open template '%s': %s" %
                                    (path, repr(ex)))
        return self._template

    @property
    def customize_conf(self):
        if self._customize_conf is None:
            path = os.path.join(self._build_json_dir, self._customize_conf_path)
            logger.info('loading customize conf from path %s', path)
            try:
                with open(path, "r") as fp:
                    self._customize_conf = json.load(fp)
            except IOError:
                # File not found, which is perfectly fine. Set to empty dict
                logger.info('failed to find customize conf from path %s', path)
                self._customize_conf = {}
        return self._customize_conf

    def remove_plugin(self, phase, name, reason=None):
        """
        if config contains plugin, remove it
        """
        for p in self.template[phase]:
            if p.get('name') == name:
                self.template[phase].remove(p)
                if reason:
                    logger.info('Removing {0}:{1}, {2}'.format(phase, name, reason))
                break

    def add_plugin(self, phase, name, args, reason=None):
        """
        if config has plugin, override it, else add it
        """
        plugin_modified = False

        for plugin in self.template[phase]:
            if plugin['name'] == name:
                plugin['args'] = args
                plugin_modified = True

        if not plugin_modified:
            self.template[phase].append({"name": name, "args": args})
            if reason:
                logger.info('{0}:{1} with args {2}, {3}'.format(phase, name, args, reason))

    def get_plugin_conf(self, phase, name):
        """
        Return the configuration for a plugin.

        Raises KeyError if there are no plugins of that type.
        Raises IndexError if the named plugin is not listed.
        """
        match = [x for x in self.template[phase] if x.get('name') == name]
        return match[0]

    def has_plugin_conf(self, phase, name):
        """
        Check whether a plugin is configured.
        """
        try:
            self.get_plugin_conf(phase, name)
            return True
        except (KeyError, IndexError):
            return False

    def _get_plugin_conf_or_fail(self, phase, name):
        try:
            conf = self.get_plugin_conf(phase, name)
        except KeyError:
            raise RuntimeError("Invalid template: plugin phase '%s' misses" % phase)
        except IndexError:
            raise RuntimeError("no such plugin in template: \"%s\"" % name)
        return conf

    def set_plugin_arg(self, phase, name, arg_key, arg_value):
        plugin_conf = self._get_plugin_conf_or_fail(phase, name)
        plugin_conf.setdefault("args", {})
        plugin_conf['args'][arg_key] = arg_value

    def set_plugin_arg_valid(self, phase, plugin, name, value):
        if value is not None:
            self.set_plugin_arg(phase, plugin, name, value)
            return True
        return False

    def to_json(self):
        return json.dumps(self.template)


class PluginsConfiguration(object):
    def __init__(self, user_params):
        # Figure out inner template to use from user_params:
        self.user_params = user_params

        #    <build_type>_inner:<arrangement_version>.json
        arrangement_version = self.user_params.arrangement_version.value
        build_type = self.user_params.build_type.value
        pt_path = '{0}_inner:{1}.json'.format(build_type, arrangement_version)
        self.pt = PluginsTemplate(self.user_params.build_json_dir.value, pt_path,
                                  self.user_params.customize_conf_path.value)

    def has_tag_suffixes_placeholder(self):
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.pt.has_plugin_conf(phase, plugin):
            logger.debug('no tag suffix placeholder')
            return False

        placeholder = '{{TAG_SUFFIXES}}'
        plugin_conf = self.pt.get_plugin_conf('postbuild_plugins', 'tag_from_config')
        return plugin_conf.get('args', {}).get('tag_suffixes') == placeholder

    def adjust_for_scratch(self):
        """
        Remove certain plugins in order to handle the "scratch build"
        scenario. Scratch builds must not affect subsequent builds,
        and should not be imported into Koji.
        """
        if self.user_params.scratch.value:
            remove_plugins = [
                ("prebuild_plugins", "koji_parent"),
                ("prebuild_plugins", "check_and_set_platforms"),  # don't override arch_override
                ("postbuild_plugins", "compress"),  # required only to make an archive for Koji
                ("postbuild_plugins", "pulp_pull"),  # required only to make an archive for Koji
                ("postbuild_plugins", "koji_upload"),
                ("postbuild_plugins", "fetch_worker_metadata"),
                ("postbuild_plugins", "compare_components"),
                ("postbuild_plugins", "import_image"),
                ("exit_plugins", "koji_promote"),
                ("exit_plugins", "koji_import"),
                ("exit_plugins", "koji_tag_build"),
                ("exit_plugins", "remove_worker_metadata"),
                ("exit_plugins", "import_image"),
                ("prebuild_plugins", "check_and_set_rebuild"),
                ("prebuild_plugins", "stop_autorebuild_if_disabled")
            ]

            if not self.has_tag_suffixes_placeholder():
                remove_plugins.append(("postbuild_plugins", "tag_from_config"))

            for when, which in remove_plugins:
                self.pt.remove_plugin(when, which, 'removed from scratch build request')

    def adjust_for_isolated(self):
        """
        Remove certain plugins in order to handle the "isolated build"
        scenario.
        """
        if self.user_params.isolated.value:
            remove_plugins = [
                ("prebuild_plugins", "check_and_set_platforms"),  # don't override arch_override
                ("prebuild_plugins", "check_and_set_rebuild"),
                ("prebuild_plugins", "stop_autorebuild_if_disabled")
            ]

            for when, which in remove_plugins:
                self.pt.remove_plugin(when, which, 'removed from isolated build request')

    def is_custom_base_image(self):
        return bool(re.match('^koji/image-build(:.*)?$',
                             self.user_params.base_image.value or ''))

    def adjust_for_custom_base_image(self):
        """
        Disable plugins to handle builds depending on whether
        or not this is a build from a custom base image.
        """
        plugins = []
        if self.is_custom_base_image():
            # Plugins irrelevant to building base images.
            plugins.append(("prebuild_plugins", "pull_base_image"))
            plugins.append(("prebuild_plugins", "koji_parent"))
            plugins.append(("prebuild_plugins", "inject_parent_image"))
            plugins.append(("prebuild_plugins", "check_and_set_rebuild"))
            plugins.append(("prebuild_plugins", "stop_autorebuild_if_disabled"))
            msg = 'removed from custom image build request'

        else:
            # Plugins not needed for building non base images.
            plugins.append(("prebuild_plugins", "add_filesystem"))
            msg = 'removed from non custom image build request'

        for when, which in plugins:
            self.pt.remove_plugin(when, which, msg)

    def render_add_filesystem(self):
        phase = 'prebuild_plugins'
        plugin = 'add_filesystem'

        if self.pt.has_plugin_conf(phase, plugin):
            self.pt.set_plugin_arg_valid(phase, plugin, 'repos',
                                         self.user_params.yum_repourls.value)
            self.pt.set_plugin_arg_valid(phase, plugin, 'from_task_id',
                                         self.user_params.filesystem_koji_task_id.value)
            self.pt.set_plugin_arg_valid(phase, plugin, 'architecture',
                                         self.user_params.platform.value)

    def render_add_labels_in_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'add_labels_in_dockerfile'
        if self.pt.has_plugin_conf(phase, plugin):
            if self.user_params.release.value:
                release_label = {'release': self.user_params.release.value}
                self.pt.set_plugin_arg(phase, plugin, 'labels', release_label)

    def render_add_yum_repo_by_url(self):
        if self.pt.has_plugin_conf('prebuild_plugins', "add_yum_repo_by_url"):
            self.pt.set_plugin_arg_valid('prebuild_plugins', "add_yum_repo_by_url", "repourls",
                                         self.user_params.yum_repourls.value)

    def render_customizations(self):
        """
        Customize template for site user specified customizations
        """
        disable_plugins = self.pt.customize_conf.get('disable_plugins', [])
        if not disable_plugins:
            logger.debug('No site-user specified plugins to disable')
        else:
            for plugin in disable_plugins:
                try:
                    self.pt.remove_plugin(plugin['plugin_type'], plugin['plugin_name'],
                                          'disabled at user request')
                except KeyError:
                    # Malformed config
                    logger.info('Invalid custom configuration found for disable_plugins')

        enable_plugins = self.pt.customize_conf.get('enable_plugins', [])
        if not enable_plugins:
            logger.debug('No site-user specified plugins to enable"')
        else:
            for plugin in enable_plugins:
                try:
                    msg = 'enabled at user request'
                    self.pt.add_plugin(plugin['plugin_type'], plugin['plugin_name'],
                                       plugin['plugin_args'], msg)
                except KeyError:
                    # Malformed config
                    logger.info('Invalid custom configuration found for enable_plugins')

    def render_flatpak_create_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'flatpak_create_dockerfile'

        if self.pt.has_plugin_conf(phase, plugin):

            if not self.user_params.flatpak.value:
                self.pt.remove_plugin(phase, plugin)
                return

            if not self.pt.set_plugin_arg_valid(phase, plugin, 'base_image',
                                                self.user_params.flatpak_base_image.value):
                self.pt.remove_plugin(phase, plugin, 'unable to set flatpak base image')

    def render_flatpak_create_oci(self):
        phase = 'prepublish_plugins'
        plugin = 'flatpak_create_oci'

        if not self.user_params.flatpak.value:
            self.pt.remove_plugin(phase, plugin)

    def render_koji(self):
        """
        if there is yum repo in user params, don't pick stuff from koji
        """
        phase = 'prebuild_plugins'
        plugin = 'koji'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        if self.user_params.yum_repourls.value:
            self.pt.remove_plugin(phase, plugin, 'there is a yum repo user parameter')
        elif self.user_params.flatpak.value:
            self.pt.remove_plugin(phase, plugin, 'flatpak build requested')
        elif not self.pt.set_plugin_arg_valid(phase, plugin, "target",
                                              self.user_params.koji_target.value):
            self.pt.remove_plugin(phase, plugin, 'no koji target supplied in user parameters')

    def render_bump_release(self):
        """
        If the bump_release plugin is present, configure it
        """
        phase = 'prebuild_plugins'
        plugin = 'bump_release'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        if self.user_params.release.value:
            self.pt.remove_plugin(phase, plugin, 'release value supplied as user parameter')
            return

        # For flatpak, we want a name-version-release of
        # <name>-<stream>-<module_build_version>.<n>, where the .<n> makes
        # sure that the build is unique in Koji
        if self.user_params.flatpak.value:
            self.pt.set_plugin_arg(phase, plugin, 'append', True)

    def render_check_and_set_platforms(self):
        """
        If the check_and_set_platforms plugin is present, configure it
        """
        phase = 'prebuild_plugins'
        plugin = 'check_and_set_platforms'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        if not self.pt.set_plugin_arg_valid(phase, plugin, "koji_target",
                                            self.user_params.koji_target.value):
            self.pt.remove_plugin(phase, plugin, 'no koji target supplied in user parameters')

    def render_import_image(self, use_auth=None):
        """
        Configure the import_image plugin
        """
        # import_image is a multi-phase plugin
        if self.user_params.imagestream_name.value is None:
            self.pt.remove_plugin('exit_plugins', 'import_image',
                                  'imagestream not in user parameters')
        elif self.pt.has_plugin_conf('exit_plugins', 'import_image'):
            self.pt.set_plugin_arg('exit_plugins', 'import_image', 'imagestream',
                                   self.user_params.imagestream_name.value)

    def render_inject_parent_image(self):
        phase = 'prebuild_plugins'
        plugin = 'inject_parent_image'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        koji_parent_build = self.user_params.koji_parent_build.value

        if not koji_parent_build:
            self.pt.remove_plugin(phase, plugin, 'no koji parent build in user parameters')
            return

        self.pt.set_plugin_arg(phase, plugin, 'koji_parent_build', koji_parent_build)

    def render_koji_upload(self, use_auth=None):
        phase = 'postbuild_plugins'
        name = 'koji_upload'
        if not self.pt.has_plugin_conf(phase, name):
            return

        def set_arg(arg, value):
            self.pt.set_plugin_arg(phase, name, arg, value)

        set_arg('koji_upload_dir', self.user_params.koji_upload_dir.value)
        set_arg('platform', self.user_params.platform.value)
        set_arg('report_multiple_digests', True)

    def render_koji_tag_build(self):
        phase = 'exit_plugins'
        plugin = 'koji_tag_build'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        if not self.user_params.koji_target.value:
            self.pt.remove_plugin(phase, plugin, 'no koji target in user parameters')
            return

        self.pt.set_plugin_arg(phase, plugin, 'target', self.user_params.koji_target.value)

    def render_orchestrate_build(self):
        phase = 'buildstep_plugins'
        plugin = 'orchestrate_build'
        if not self.pt.has_plugin_conf(phase, plugin):
            return

        # Parameters to be used in call to create_worker_build
        worker_params = [
            'component', 'git_branch', 'git_ref', 'git_uri', 'koji_task_id',
            'filesystem_koji_task_id', 'scratch', 'koji_target', 'user', 'yum_repourls',
            'arrangement_version', 'koji_parent_build', 'isolated', 'reactor_config_map',
            'reactor_config_override'
        ]

        build_kwargs = self.user_params.to_dict(worker_params)
        # koji_target is passed as target for some reason
        build_kwargs['target'] = build_kwargs.pop('koji_target', None)

        if self.user_params.flatpak.value:
            build_kwargs['flatpak'] = True

        self.pt.set_plugin_arg_valid(phase, plugin, 'platforms', self.user_params.platforms.value)
        self.pt.set_plugin_arg(phase, plugin, 'build_kwargs', build_kwargs)

        # Parameters to be used as Configuration overrides for each worker
        config_kwargs = {
            'flatpak_base_image': self.user_params.flatpak_base_image.value,
        }

        # Remove empty values, and always convert to string for better interaction
        # with Configuration class and JSON encoding
        config_kwargs = dict((k, str(v)) for k, v in config_kwargs.items() if v is not None)

        if not self.user_params.build_imagestream.value:
            config_kwargs['build_image'] = self.user_params.build_image.value

        self.pt.set_plugin_arg(phase, plugin, 'config_kwargs', config_kwargs)

    def render_resolve_composes(self):
        phase = 'prebuild_plugins'
        plugin = 'resolve_composes'

        if not self.pt.has_plugin_conf(phase, plugin):
            return

        if self.user_params.yum_repourls.value:
            self.pt.remove_plugin(phase, plugin, 'yum repourls specified in user parameters')
            return

        self.pt.set_plugin_arg_valid(phase, plugin, 'compose_ids',
                                     self.user_params.compose_ids.value)

        self.pt.set_plugin_arg_valid(phase, plugin, 'signing_intent',
                                     self.user_params.signing_intent.value)

        self.pt.set_plugin_arg_valid(phase, plugin, 'koji_target',
                                     self.user_params.koji_target.value)

    def render_resolve_module_compose(self):
        phase = 'prebuild_plugins'
        plugin = 'resolve_module_compose'

        if self.pt.has_plugin_conf(phase, plugin):
            if not self.user_params.flatpak.value:
                self.pt.remove_plugin(phase, plugin)
                return

            self.pt.set_plugin_arg_valid(phase, plugin, 'compose_ids',
                                         self.user_params.compose_ids.value)

    def render_squash(self):
        phase = 'prepublish_plugins'
        plugin = 'squash'

        if self.user_params.flatpak.value:
            # We'll extract the filesystem anyways for a Flatpak instead of exporting
            # the docker image directly, so squash just slows things down.
            self.pt.remove_plugin(phase, plugin, 'flatpak build requested')
            return

    def render_tag_from_config(self):
        """Configure tag_from_config plugin"""
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.has_tag_suffixes_placeholder():
            return

        repo_info = utils.get_repo_info(self.user_params.git_uri.value,
                                        self.user_params.git_ref.value,
                                        git_branch=self.user_params.git_branch.value)

        unique_tag = self.user_params.image_tag.value.split(':')[-1]
        tag_suffixes = {'unique': [unique_tag], 'primary': []}

        if self.user_params.build_type.value == BUILD_TYPE_ORCHESTRATOR:
            if self.user_params.scratch.value:
                pass
            elif self.user_params.isolated.value:
                tag_suffixes['primary'].extend(['{version}-{release}'])
            elif repo_info.additional_tags.from_container_yaml:
                tag_suffixes['primary'].extend(['{version}-{release}'])
                tag_suffixes['primary'].extend(repo_info.additional_tags.tags)
            else:
                tag_suffixes['primary'].extend(['latest', '{version}', '{version}-{release}'])
                tag_suffixes['primary'].extend(repo_info.additional_tags.tags)

        self.pt.set_plugin_arg(phase, plugin, 'tag_suffixes', tag_suffixes)

    def render(self):
        self.user_params.validate()
        # adjust for custom configuration first
        self.render_customizations()

        self.adjust_for_scratch()
        self.adjust_for_isolated()
        self.adjust_for_custom_base_image()

        # Set parameters on each plugin as needed
        self.render_add_filesystem()
        self.render_add_labels_in_dockerfile()
        self.render_add_yum_repo_by_url()
        self.render_bump_release()
        self.render_check_and_set_platforms()
        self.render_flatpak_create_dockerfile()
        self.render_flatpak_create_oci()
        self.render_import_image()
        self.render_inject_parent_image()
        self.render_koji()
        self.render_koji_tag_build()
        self.render_koji_upload()
        self.render_orchestrate_build()
        self.render_resolve_composes()
        self.render_resolve_module_compose()
        self.render_squash()
        self.render_tag_from_config()
        return self.pt.to_json()
