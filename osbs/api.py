"""
Copyright (c) 2015-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, unicode_literals, absolute_import

from collections import namedtuple
import logging
import sys
import warnings
import yaml
from functools import wraps
from typing import Any, Dict
from string import Template

from osbs.build.user_params import (
    BuildUserParams,
    SourceContainerUserParams
)
from osbs.constants import (RELEASE_LABEL_FORMAT, VERSION_LABEL_FORBIDDEN_CHARS,
                            ISOLATED_RELEASE_FORMAT)
from osbs.tekton import Openshift, PipelineRun
from osbs.exceptions import (OsbsException, OsbsValidationException, OsbsResponseException)
from osbs.utils.labels import Labels
# import utils in this way, so that we can mock standalone functions with flexmock
from osbs import utils


def _load_pipeline_from_template(pipeline_run_path, substitutions):
    """Load pipeline run from template and apply substitutions"""
    with open(pipeline_run_path) as f:
        yaml_data = f.read()
    template = Template(yaml_data)
    return yaml.safe_load(template.safe_substitute(substitutions))


# Decorator for API methods.
def osbsapi(func):
    @wraps(func)
    def catch_exceptions(*args, **kwargs):
        if kwargs.pop("namespace", None):
            warnings.warn("OSBS.%s: the 'namespace' argument is no longer supported" %
                          func.__name__)
        try:
            return func(*args, **kwargs)
        except OsbsException:
            # Re-raise OsbsExceptions
            raise
        except Exception as ex:
            # Propogate flexmock errors immediately (used in test cases)
            if getattr(ex, '__module__', None) == 'flexmock':
                raise

            # Convert anything else to OsbsException

            # Python 3 has implicit exception chaining and enhanced
            # reporting, so you get the original traceback as well as
            # the one originating here.
            # For Python 2, let's do that explicitly.
            raise OsbsException(cause=ex, traceback=sys.exc_info()[2])

    return catch_exceptions


_REQUIRED_PARAM = object()

logger = logging.getLogger(__name__)

LogEntry = namedtuple('LogEntry', ['platform', 'line'])


class OSBS(object):

    _GIT_LABEL_KEYS = ('git-repo-name', 'git-branch', 'git-full-repo')
    _OLD_LABEL_KEYS = ('git-repo-name', 'git-branch')

    @osbsapi
    def __init__(self, openshift_configuration):
        """ """
        self.os_conf = openshift_configuration
        self.os = Openshift(openshift_api_url=self.os_conf.get_openshift_base_uri(),
                            openshift_oauth_url=self.os_conf.get_openshift_oauth_api_uri(),
                            k8s_api_url=self.os_conf.get_k8s_api_uri(),
                            verbose=self.os_conf.get_verbosity(),
                            username=self.os_conf.get_username(),
                            password=self.os_conf.get_password(),
                            use_kerberos=self.os_conf.get_use_kerberos(),
                            client_cert=self.os_conf.get_client_cert(),
                            client_key=self.os_conf.get_client_key(),
                            kerberos_keytab=self.os_conf.get_kerberos_keytab(),
                            kerberos_principal=self.os_conf.get_kerberos_principal(),
                            kerberos_ccache=self.os_conf.get_kerberos_ccache(),
                            use_auth=self.os_conf.get_use_auth(),
                            verify_ssl=self.os_conf.get_verify_ssl(),
                            token=self.os_conf.get_oauth2_token(),
                            namespace=self.os_conf.get_namespace())
        self._bm = None

    def _check_labels(self, repo_info):
        labels = repo_info.labels

        required_missing = False
        req_labels = {}
        # required labels which needs to have explicit value (not from env variable)
        explicit_labels = [Labels.LABEL_TYPE_NAME,
                           Labels.LABEL_TYPE_COMPONENT]
        # version label isn't used here, but is required label in Dockerfile
        # and is used and required for atomic reactor
        # if we don't catch error here, it will fail in atomic reactor later
        for label in [Labels.LABEL_TYPE_NAME,
                      Labels.LABEL_TYPE_COMPONENT,
                      Labels.LABEL_TYPE_VERSION]:
            try:
                _, req_labels[label] = labels.get_name_and_value(label)

                if label in explicit_labels and not req_labels[label]:
                    required_missing = True
                    logger.error("required label doesn't have explicit value in Dockerfile : %s",
                                 labels.get_name(label))
            except KeyError:
                required_missing = True
                logger.error("required label missing from Dockerfile : %s",
                             labels.get_name(label))

        try:
            _, release_value = labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            if release_value and not RELEASE_LABEL_FORMAT.match(release_value):
                logger.error("release label '%s' doesn't match regex : %s", release_value,
                             RELEASE_LABEL_FORMAT.pattern)
                raise OsbsValidationException("release label doesn't have proper format")
        except KeyError:
            pass

        try:
            _, version_value = labels.get_name_and_value(Labels.LABEL_TYPE_VERSION)
        # version doesn't exist
        except KeyError:
            pass
        else:
            if version_value:
                wrong_chars = \
                    [denied for denied in VERSION_LABEL_FORBIDDEN_CHARS if denied in version_value]

                if wrong_chars:
                    msg = "version label '{}' contains not allowed chars : '{}'".\
                        format(version_value, wrong_chars)
                    logger.error(msg)
                    raise OsbsValidationException(msg)

        if required_missing:
            raise OsbsValidationException("required label missing from Dockerfile")

        # Verify the name label meets requirements.
        # It is made up of slash-separated name components.
        #
        # When pulling an image, the first component of the name
        # pulled is interpreted as a registry name if it contains a
        # '.' character, and otherwise the configured registries are
        # queried in turn.
        #
        # Due to this, a name with '.' in its initial component will
        # be awkward to pull from a registry because the registry name
        # will have to be explicitly supplied, e.g. "docker pull
        # foo.bar/baz" will fail because the "foo.bar" registry cannot
        # be contacted.
        #
        # Avoid this awkwardness by forbidding '.' in the initial
        # component of the image name.
        name_components = req_labels[Labels.LABEL_TYPE_NAME].split('/', 1)
        if '.' in name_components[0]:
            raise OsbsValidationException("initial image name component "
                                          "must not contain '.'")

        return req_labels

    # Gives flexmock something to mock
    def get_user_params(self, component=None, req_labels=None, **kwargs):
        req_labels = req_labels or {}
        user_component = component or req_labels[Labels.LABEL_TYPE_COMPONENT]
        return BuildUserParams.make_params(build_conf=self.os_conf,
                                           component=user_component,
                                           name_label=req_labels[Labels.LABEL_TYPE_NAME],
                                           **kwargs)

    def _checks_for_isolated(self, user_params):
        if user_params.isolated:
            if not user_params.release:
                raise OsbsValidationException(
                    'The release parameter is required for isolated builds.')

            if not ISOLATED_RELEASE_FORMAT.match(user_params.release):
                raise OsbsValidationException(
                    'For isolated builds, the release value must be in the format: {}'
                    .format(ISOLATED_RELEASE_FORMAT.pattern))

    def _checks_for_flatpak(self, flatpak, repo_info):
        if flatpak and not repo_info.configuration.is_flatpak:
            raise OsbsException(
                "Flatpak build, "
                "but repository doesn't have a container.yaml with a flatpak: section")

        if not flatpak and repo_info.configuration.is_flatpak:
            raise OsbsException(
                "Not a flatpak build, "
                "but repository has a container.yaml with a flatpak: section")

    def _get_pipeline_template_substitutions(
            self, *, user_params, pipeline_run_name) -> Dict[str, str]:
        """Return map of substitutions used by pipeline run template
        to construct pipeline run data

        These substitutions can be used in pipeline run template

        Substitutions:
          $osbs_configmap_name - name of configmap defined by user
          $osbs_namespace - namespace where pipeline runs
          $osbs_pipeline_run_name - name of pipeline run
          $osbs_user_params_json - user params in json format
        """
        return {
            'osbs_configmap_name': user_params.reactor_config_map,
            'osbs_namespace': self.os_conf.get_namespace(),
            'osbs_pipeline_run_name': pipeline_run_name,
            'osbs_user_params_json': user_params.to_json(),
        }

    def _get_binary_container_pipeline_name(self, user_params):
        pipeline_run_postfix = utils.generate_random_postfix()
        pipeline_run_name = user_params.name

        if user_params.isolated:
            pipeline_run_name = f'isolated-{pipeline_run_postfix}'

        elif user_params.scratch:
            pipeline_run_name = f'scratch-{pipeline_run_postfix}'
        return pipeline_run_name

    def _get_binary_container_pipeline_data(self, *, user_params, pipeline_run_name):
        pipeline_run_path = self.os_conf.get_pipeline_run_path()

        substitutions = self._get_pipeline_template_substitutions(
            user_params=user_params,
            pipeline_run_name=pipeline_run_name
        )
        pipeline_run_data = _load_pipeline_from_template(pipeline_run_path, substitutions)

        return pipeline_run_data

    @osbsapi
    def create_binary_container_build(self, **kwargs):
        return self.create_binary_container_pipeline_run(**kwargs)

    @osbsapi
    def create_binary_container_pipeline_run(self,
                                             git_uri=_REQUIRED_PARAM, git_ref=_REQUIRED_PARAM,
                                             git_branch=_REQUIRED_PARAM,
                                             component=None,
                                             flatpak=None,
                                             git_commit_depth=None,
                                             isolated=None,
                                             koji_task_id=None,
                                             target=None,
                                             operator_csv_modifications_url=None,
                                             **kwargs):

        required_params = {"git_uri": git_uri, "git_ref": git_ref, "git_branch": git_branch}
        missing_params = []
        for param_name, param_arg in required_params.items():
            if param_arg is _REQUIRED_PARAM or not param_arg:
                missing_params.append(param_name)
        if missing_params:
            raise OsbsException('required parameter {} missing'.format(", ".join(missing_params)))

        if operator_csv_modifications_url and not isolated:
            raise OsbsException('Only isolated build can update operator CSV metadata')

        repo_info = utils.get_repo_info(git_uri, git_ref, git_branch=git_branch,
                                        depth=git_commit_depth)

        self._checks_for_flatpak(flatpak, repo_info)

        req_labels = self._check_labels(repo_info)

        user_params = self.get_user_params(
            base_image=repo_info.base_image,
            component=component,
            flatpak=flatpak,
            isolated=isolated,
            koji_target=target,
            koji_task_id=koji_task_id,
            req_labels=req_labels,
            repo_info=repo_info,
            operator_csv_modifications_url=operator_csv_modifications_url,
            **kwargs)

        self._checks_for_isolated(user_params)

        pipeline_run_name = self._get_binary_container_pipeline_name(user_params)
        pipeline_run_data = self._get_binary_container_pipeline_data(
            user_params=user_params,
            pipeline_run_name=pipeline_run_name)

        logger.info("creating binary container image pipeline run: %s", pipeline_run_name)

        pipeline_run = PipelineRun(self.os, pipeline_run_name, pipeline_run_data)

        try:
            logger.info("pipeline run created: %s", pipeline_run.start_pipeline_run())
        except OsbsResponseException:
            logger.error("failed to create pipeline run %s", pipeline_run_name)
            raise

        return pipeline_run

    def _get_source_container_pipeline_name(self):
        pipeline_run_postfix = utils.generate_random_postfix()
        pipeline_run_name = f'source-{pipeline_run_postfix}'
        return pipeline_run_name

    def _get_source_container_pipeline_data(self, *, user_params, pipeline_run_name):

        pipeline_run_path = self.os_conf.get_pipeline_run_path()

        substitutions = self._get_pipeline_template_substitutions(
            user_params=user_params,
            pipeline_run_name=pipeline_run_name,
        )
        pipeline_run_data = _load_pipeline_from_template(pipeline_run_path, substitutions)

        return pipeline_run_data

    @osbsapi
    def create_source_container_build(self, **kwargs):
        return self.create_source_container_pipeline_run(**kwargs)

    @osbsapi
    def create_source_container_pipeline_run(self,
                                             component=None,
                                             koji_task_id=None,
                                             target=None,
                                             **kwargs):
        """
        Take input args, create source pipeline run

        :return: instance of PiplelineRun
        """
        error_messages = []
        # most likely can be removed, source build should get component name
        # from binary build OSBS2 TBD
        if not component:
            error_messages.append("required argument 'component' can't be empty")
        if error_messages:
            raise OsbsValidationException(", ".join(error_messages))

        user_params = SourceContainerUserParams.make_params(
            build_conf=self.os_conf,
            component=component,
            koji_target=target,
            koji_task_id=koji_task_id,
            **kwargs
        )

        pipeline_run_name = self._get_source_container_pipeline_name()
        pipeline_run_data = self._get_source_container_pipeline_data(
            user_params=user_params,
            pipeline_run_name=pipeline_run_name,
        )

        logger.info("creating source container image pipeline run: %s", pipeline_run_name)

        pipeline_run = PipelineRun(self.os, pipeline_run_name, pipeline_run_data)

        try:
            logger.info("pipeline run created: %s", pipeline_run.start_pipeline_run())
        except OsbsResponseException:
            logger.error("failed to create pipeline run %s", pipeline_run_name)
            raise

        return pipeline_run

    @osbsapi
    def get_build_name(self, build_response: PipelineRun):
        return build_response.pipeline_run_name

    @osbsapi
    def get_build(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.get_info()

    @osbsapi
    def get_final_platforms(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.get_final_platforms()

    @osbsapi
    def get_build_reason(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.status_reason

    @osbsapi
    def build_has_succeeded(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.has_succeeded()

    @osbsapi
    def build_not_finished(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.has_not_finished()

    @osbsapi
    def wait_for_build_to_finish(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.wait_for_finish()

    @osbsapi
    def build_was_cancelled(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.was_cancelled()

    @osbsapi
    def build_has_any_failed_tasks(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.any_task_failed()

    @osbsapi
    def build_has_any_cancelled_tasks(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.any_task_was_cancelled()

    @osbsapi
    def cancel_build(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.cancel_pipeline_run()

    @osbsapi
    def remove_build(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.remove_pipeline_run()

    @osbsapi
    def get_build_logs(self, build_name, follow=False, wait=False):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.get_logs(follow=follow, wait=wait)

    @osbsapi
    def get_build_error_message(self, build_name):
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.get_error_message()

    @osbsapi
    def get_build_results(self, build_name) -> Dict[str, Any]:
        """Fetch the pipelineResults for this build."""
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.pipeline_results

    @osbsapi
    def get_task_results(self, build_name) -> Dict[str, Any]:
        """Fetch tasks results for this build."""
        pipeline_run = PipelineRun(self.os, build_name)
        return pipeline_run.get_task_results()
