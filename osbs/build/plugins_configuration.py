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
import copy

from osbs.constants import (BUILD_TYPE_ORCHESTRATOR, ISOLATED_RELEASE_FORMAT)
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.utils import Labels

logger = logging.getLogger(__name__)


class PluginsConfiguration(object):
    def __init__(self, user_params):
        # Figure out inner template to use from user_params:
        self.user_params = user_params

        #    <build_type>_inner:<arrangement_version>.json
        arrangement_version = self.user_params.arrangement_version.value
        build_type = self.user_params.build_type.value
        self._template_path = '{0}_inner:{1}.json'.format(build_type, arrangement_version)
        self._template = None

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.user_params.build_json_dir.value, self._template_path)
            logger.debug("loading template from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._template = json.load(fp)
            except (IOError, OSError) as ex:
                raise OsbsException("Can't open template '%s': %s" %
                                    (path, repr(ex)))
        return self._template

    def remove_plugin(self, plugin_type, plugin_name):
        """
        if config contains plugin, remove it
        """
        for p in self.template[plugin_type]:
            if p.get('name') == plugin_name:
                self.template[plugin_type].remove(p)
                break

    def add_plugin(self, plugin_type, plugin_name, args_dict):
        """
        if config has plugin, override it, else add it
        """
        plugin_modified = False

        for plugin in self.template[plugin_type]:
            if plugin['name'] == plugin_name:
                plugin['args'] = args_dict
                plugin_modified = True

        if not plugin_modified:
            self.template[plugin_type].append({"name": plugin_name, "args": args_dict})

    def template_get_plugin_conf(self, plugin_type, plugin_name):
        """
        Return the configuration for a plugin.

        Raises KeyError if there are no plugins of that type.
        Raises IndexError if the named plugin is not listed.
        """
        match = [x for x in self.template[plugin_type] if x.get('name') == plugin_name]
        return match[0]

    def template_has_plugin_conf(self, plugin_type, plugin_name):
        """
        Check whether a plugin is configured.
        """
        try:
            self.template_get_plugin_conf(plugin_type, plugin_name)
            return True
        except (KeyError, IndexError):
            return False

    def _template_get_plugin_conf_or_fail(self, plugin_type, plugin_name):
        try:
            conf = self.template_get_plugin_conf(plugin_type, plugin_name)
        except KeyError:
            raise RuntimeError("Invalid template: plugin type '%s' misses" % plugin_type)
        except IndexError:
            raise RuntimeError("no such plugin in template: \"%s\"" % plugin_name)
        return conf

    def template_set_param(self, param, value):
        self.template[param] = value

    def template_merge_arg(self, plugin_type, plugin_name, arg_key, arg_dict):
        plugin_conf = self._template_get_plugin_conf_or_fail(plugin_type, plugin_name)

        # Values supplied by the caller override those from the template JSON
        template_value = plugin_conf['args'].get(arg_key, {})
        if not isinstance(template_value, dict):
            template_value = {}

        value = copy.deepcopy(template_value)
        value.update(arg_dict)
        plugin_conf['args'][arg_key] = value

    def template_set_arg(self, plugin_type, plugin_name, arg_key, arg_value):
        plugin_conf = self._template_get_plugin_conf_or_fail(plugin_type, plugin_name)
        plugin_conf.setdefault("args", {})
        plugin_conf['args'][arg_key] = arg_value

    def set_plugin_arg_valid(self, phase, plugin, name, value):
        if value is not None:
            self.template_set_arg(phase, plugin, name, value)
            return True
        return False

    def has_tag_suffixes_placeholder(self):
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.template_has_plugin_conf(phase, plugin):
            return False

        placeholder = '{{TAG_SUFFIXES}}'
        plugin_conf = self.template_get_plugin_conf('postbuild_plugins', 'tag_from_config')
        return plugin_conf.get('args', {}).get('tag_suffixes') == placeholder

    def adjust_for_scratch(self):
        """
        Remove certain plugins in order to handle the "scratch build"
        scenario. Scratch builds must not affect subsequent builds,
        and should not be imported into Koji.
        """
        if self.user_params.scratch.value:
            self.template['user_params'].pop('triggers', None)

            remove_plugins = [
                ("prebuild_plugins", "koji_parent"),
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
            ]

            if not self.has_tag_suffixes_placeholder():
                remove_plugins.append(("postbuild_plugins", "tag_from_config"))

            for when, which in remove_plugins:
                logger.info("removing %s from scratch build request", which)
                self.remove_plugin(when, which)

            if self.template_has_plugin_conf('postbuild_plugins', 'tag_by_labels'):
                self.template_set_arg('postbuild_plugins', 'tag_by_labels',
                                      'unique_tag_only', True)

            self.set_label('scratch', 'true')

    def adjust_for_isolated(self, release):
        if not self.isolated:
            return

        self.template['user_params'].pop('triggers', None)

        if not release.value:
            raise OsbsValidationException('The release parameter is required for isolated builds.')

        if not ISOLATED_RELEASE_FORMAT.match(release.value):
            raise OsbsValidationException(
                'For isolated builds, the release value must be in the format: {0}'
                .format(ISOLATED_RELEASE_FORMAT.pattern))

        self.set_label('isolated', 'true')
        self.set_label('isolated-release', release.value)

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
            msg = "removing %s from custom image build request"

        else:
            # Plugins not needed for building non base images.
            plugins.append(("prebuild_plugins", "add_filesystem"))
            msg = "removing %s from non custom image build request"

        for when, which in plugins:
            logger.info(msg, which)
            self.remove_plugin(when, which)

    def adjust_for_repo_info(self):
        if not self._repo_info:
            logger.warning('repo info not set')
            return

        if not self._repo_info.configuration.is_autorebuild_enabled():
            logger.info('autorebuild is disabled in repo configuration, removing triggers')
            self.template['user_params'].pop('triggers', None)

        else:
            labels = Labels(self._repo_info.dockerfile_parser.labels)
            try:
                labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            except KeyError:
                # As expected, release label not set in Dockerfile
                pass
            else:
                raise RuntimeError('when autorebuild is enabled in repo configuration, '
                                   '"release" label must not be set in Dockerfile')

    def render_add_filesystem(self):
        phase = 'prebuild_plugins'
        plugin = 'add_filesystem'

        if self.template_has_plugin_conf(phase, plugin):
            self.set_plugin_arg_valid(phase, plugin, 'repos',
                                      self.user_params.yum_repourls.value)
            self.set_plugin_arg_valid(phase, plugin, 'from_task_id',
                                      self.user_params.filesystem_koji_task_id.value)

    def render_add_labels_in_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'add_labels_in_dockerfile'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        implicit_labels = {}
        label_user_params = {
            'release': self.user_params.release,
        }

        for label, user_params in label_user_params.items():
            if user_params.value is not None:
                implicit_labels[label] = user_params.value

        self.template_merge_arg(phase, plugin, 'labels', implicit_labels)

    def render_add_yum_repo_by_url(self):
        if (self.user_params.yum_repourls.value is not None and
                self.template_has_plugin_conf('prebuild_plugins', "add_yum_repo_by_url")):
            self.template_set_arg('prebuild_plugins', "add_yum_repo_by_url", "repourls",
                                  self.user_params.yum_repourls.value)

    def render_customizations(self):
        """
        Customize prod_inner for site user_paramsific customizations
        """

        disable_plugins = self.customize_conf.get('disable_plugins', [])
        if not disable_plugins:
            logger.debug("No site-user_paramsific plugins to disable")
        else:
            for plugin_dict in disable_plugins:
                try:
                    self.remove_plugin(
                        plugin_dict['plugin_type'],
                        plugin_dict['plugin_name']
                    )
                    logger.debug(
                        "site-user_paramsific plugin disabled -> Type:{0} Name:{1}".format(
                            plugin_dict['plugin_type'],
                            plugin_dict['plugin_name']
                        )
                    )
                except KeyError:
                    # Malformed config
                    logger.debug("Invalid custom configuration found for disable_plugins")

        enable_plugins = self.customize_conf.get('enable_plugins', [])
        if not enable_plugins:
            logger.debug("No site-user_paramsific plugins to enable")
        else:
            for plugin_dict in enable_plugins:
                try:
                    self.add_plugin(
                        plugin_dict['plugin_type'],
                        plugin_dict['plugin_name'],
                        plugin_dict['plugin_args']
                    )
                    logger.debug(
                        "site-user_paramsific plugin enabled -> Type:{0} Name:{1} Args: {2}".format(
                            plugin_dict['plugin_type'],
                            plugin_dict['plugin_name'],
                            plugin_dict['plugin_args']
                        )
                    )
                except KeyError:
                    # Malformed config
                    logger.debug("Invalid custom configuration found for enable_plugins")

    def render_flatpak_create_dockerfile(self):
        phase = 'prebuild_plugins'
        plugin = 'flatpak_create_dockerfile'

        if self.template_has_plugin_conf(phase, plugin):
            if not self.set_plugin_arg_valid(phase, plugin, 'base_image',
                                             self.user_params.flatpak_base_image.value):
                self.remove_plugin(phase, plugin)
                return

    def render_flatpak_create_oci(self):
        phase = 'prepublish_plugins'
        plugin = 'flatpak_create_oci'

        if not self.user_params.flatpak.value:
            self.remove_plugin(phase, plugin)
            return

    def render_koji(self):
        """
        if there is yum repo user_paramsified, don't pick stuff from koji
        """
        phase = 'prebuild_plugins'
        plugin = 'koji'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        if self.user_params.yum_repourls.value:
            logger.info("removing koji from request "
                        "because there is yum repo user_paramsified")
            self.remove_plugin(phase, plugin)
        elif self.user_params.flatpak.value:
            logger.info("removing koji from request "
                        "because this is a Flatpak built from a module")
            self.remove_plugin(phase, plugin)
        elif not self.set_plugin_arg_valid(phase, plugin, "target",
                                           self.user_params.koji_target.value):
            logger.info("removing koji from request as not user_paramsified")
            self.remove_plugin(phase, plugin)

    def render_bump_release(self):
        """
        If the bump_release plugin is present, configure it
        """
        phase = 'prebuild_plugins'
        plugin = 'bump_release'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        if self.user_params.release.value:
            logger.info('removing %s from request as release already user_paramsified',
                        plugin)
            self.remove_plugin(phase, plugin)
            return

        # For flatpak, we want a name-version-release of
        # <name>-<stream>-<module_build_version>.<n>, where the .<n> makes
        # sure that the build is unique in Koji
        if self.user_params.flatpak.value:
            self.template_set_arg(phase, plugin, 'append', True)

    def render_import_image(self, use_auth=None):
        """
        Configure the import_image plugin
        """
        # import_image is a multi-phase plugin
        phases = ('postbuild_plugins', 'exit_plugins')
        plugin = 'import_image'

        for phase in phases:
            if self.user_params.imagestream_name.value is None:
                logger.info("removing %s from template, imagestream is not defined", plugin)
                self.remove_plugin(phase, plugin)
                continue

            if self.template_has_plugin_conf(phase, plugin):
                self.template_set_arg(phase, plugin, 'imagestream',
                                      self.user_params.imagestream_name.value)
                self.template_set_arg(phase, plugin, 'build_json_dir',
                                      self.user_params.build_json_dir.value)

    def render_inject_parent_image(self):
        phase = 'prebuild_plugins'
        plugin = 'inject_parent_image'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        koji_parent_build = self.user_params.koji_parent_build.value

        if not koji_parent_build:
            logger.info('removing %s, koji_parent_build must be provided', plugin)
            self.remove_plugin(phase, plugin)
            return

        self.template_set_arg(phase, plugin, 'koji_parent_build', koji_parent_build)

    def render_koji_promote(self, use_auth=None):
        if not self.template_has_plugin_conf('exit_plugins', 'koji_promote'):
            return

        koji_target = self.user_params.koji_target.value
        if not self.set_plugin_arg_valid('exit_plugins', 'koji_promote', 'target', koji_target):
            logger.info("removing koji_promote from request as no kojihub "
                        "user_paramsified")
            self.remove_plugin("exit_plugins", "koji_promote")

    def render_koji_upload(self, use_auth=None):
        phase = 'postbuild_plugins'
        name = 'koji_upload'
        if not self.template_has_plugin_conf(phase, name):
            return

        def set_arg(arg, value):
            self.template_set_arg(phase, name, arg, value)

        set_arg('build_json_dir', self.user_params.build_json_dir.value)
        set_arg('platform', self.user_params.platform.value)
        set_arg('report_multiple_digests', True)

    def render_koji_import(self, use_auth=None):
        if not self.template_has_plugin_conf('exit_plugins', 'koji_import'):
            return

        koji_target = self.user_params.koji_target.value
        if not self.set_plugin_arg_valid('exit_plugins', 'koji_import', 'target', koji_target):
            logger.info("removing koji_import from request as no kojihub user_paramsified")
            self.remove_plugin("exit_plugins", "koji_import")

    def render_koji_tag_build(self):
        phase = 'exit_plugins'
        plugin = 'koji_tag_build'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        if not self.user_params.koji_target.value:
            logger.info('Removing %s because no koji_target was user_paramsified', plugin)
            self.remove_plugin(phase, plugin)
            return

        self.template_set_arg(phase, plugin, 'target', self.user_params.koji_target.value)

    def render_orchestrate_build(self):
        phase = 'buildstep_plugins'
        plugin = 'orchestrate_build'
        if not self.template_has_plugin_conf(phase, plugin):
            return

        if self.user_params.platforms.value is None:
            logger.debug('removing %s plugin: no platforms', plugin)
            self.remove_plugin(phase, plugin)
            return

        # Parameters to be used in call to create_worker_build
        build_kwargs = {
            'component': self.user_params.component.value,
            'git_branch': self.user_params.git_branch.value,
            'git_ref': self.user_params.git_ref.value,
            'git_uri': self.user_params.git_uri.value,
            'koji_task_id': self.user_params.koji_task_id.value,
            'filesystem_koji_task_id': self.user_params.filesystem_koji_task_id.value,
            'scratch': self.user_params.scratch.value,
            'target': self.user_params.koji_target.value,
            'user': self.user_params.user.value,
            'yum_repourls': self.user_params.yum_repourls.value,
            'arrangement_version': self.user_params.arrangement_version.value,
            'koji_parent_build': self.user_params.koji_parent_build.value,
            'isolated': self.user_params.isolated.value,
        }

        if self.user_params.flatpak.value:
            build_kwargs['flatpak'] = True

        self.template_set_arg(phase, plugin, 'platforms', self.user_params.platforms.value)
        self.template_set_arg(phase, plugin, 'build_kwargs', build_kwargs)

        # Parameters to be used as Configuration overrides for each worker
        config_kwargs = {
            'flatpak_base_image': self.user_params.flatpak_base_image.value,
        }

        # Remove empty values, and always convert to string for better interaction
        # with Configuration class and JSON encoding
        config_kwargs = dict((k, str(v)) for k, v in config_kwargs.items() if v is not None)

        if not self.user_params.build_imagestream.value:
            config_kwargs['build_image'] = self.user_params.build_image.value

        self.template_set_arg(phase, plugin, 'config_kwargs', config_kwargs)

    def render_resolve_composes(self):
        phase = 'prebuild_plugins'
        plugin = 'resolve_composes'

        if not self.template_has_plugin_conf(phase, plugin):
            return

        if self.user_params.yum_repourls.value:
            logger.info('removing %s from request as yum_repourls is user_paramsified', plugin)
            self.remove_plugin(phase, plugin)
            return

        self.set_plugin_arg_valid(phase, plugin, 'compose_ids',
                                  self.user_params.compose_ids.value)

        self.set_plugin_arg_valid(phase, plugin, 'signing_intent',
                                  self.user_params.signing_intent.value)

    def render_resolve_module_compose(self):
        phase = 'prebuild_plugins'
        plugin = 'resolve_module_compose'

        if self.template_has_plugin_conf(phase, plugin):
            if not self.user_params.flatpak.value:
                self.remove_plugin(phase, plugin)
                return

            self.set_plugin_arg_valid(phase, plugin, 'compose_ids',
                                      self.user_params.compose_ids.value)

    def render_squash(self):
        phase = 'prepublish_plugins'
        plugin = 'squash'

        if self.user_params.flatpak.value:
            # We'll extract the filesystem anyways for a Flatpak instead of exporting
            # the docker image directly, so squash just slows things down.
            self.remove_plugin(phase, plugin)
            return

    def render_tag_from_config(self):
        """Configure tag_from_config plugin"""
        phase = 'postbuild_plugins'
        plugin = 'tag_from_config'
        if not self.has_tag_suffixes_placeholder():
            return

        unique_tag = self.user_params.image_tag.value.split(':')[-1]
        tag_suffixes = {'unique': [unique_tag], 'primary': []}

        if self.user_params.build_type.value == BUILD_TYPE_ORCHESTRATOR:
            if self.user_params.scratch.value:
                pass
            elif self.user_params.isolated.value:
                tag_suffixes['primary'].extend(['{version}-{release}'])
            else:
                tag_suffixes['primary'].extend(['latest', '{version}', '{version}-{release}'])
                if self.user_params.additional_tags:
                    tag_suffixes['primary'].extend(self.user_params.additional_tags.tags)

        self.template_set_arg(phase, plugin, 'tag_suffixes', tag_suffixes)

    def render(self):
        # Set parameters on each plugin as needed

        self.render_add_filesystem()
        self.render_add_labels_in_dockerfile()
        self.render_add_yum_repo_by_url()
        self.render_bump_release()
        # self.render_customizations()
        self.render_flatpak_create_dockerfile()
        self.render_flatpak_create_oci()
        self.render_import_image()
        self.render_inject_parent_image()
        self.render_koji()
        self.render_koji_import()
        self.render_koji_promote()
        self.render_koji_tag_build()
        self.render_koji_upload()
        self.render_orchestrate_build()
        self.render_resolve_composes()
        self.render_resolve_module_compose()
        self.render_squash()
        self.render_tag_from_config()
        """ need to figure out how to pass this back to reality
        env_json = self.build_json['spec']['strategy']['customStrategy']['env']
        p = [env for env in env_json if env["name"] == "USER_PARAMS"]
        if len(p) <= 0:
            raise RuntimeError("\"env\" misses key USER_PARAMS")
        p[0]['value'] = json.dumps(self.dock_json)

        return p[0]['value']
        """
        return json.dumps(self.template)
