"""
Copyright (c) 2017-2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from __future__ import absolute_import

from flexmock import flexmock
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.constants import (ADDITIONAL_TAGS_FILE,
                            REPO_CONTAINER_CONFIG,)
from osbs.utils.labels import Labels
from osbs.repo_utils import RepoInfo, RepoConfiguration, AdditionalTagsConfig, ModuleSpec
from textwrap import dedent

import os
import pytest
import yaml


class TestRepoInfo(object):

    def test_default_params(self):
        repo_info = RepoInfo()
        assert repo_info.dockerfile_parser is None
        assert isinstance(repo_info.configuration, RepoConfiguration)
        assert isinstance(repo_info.additional_tags, AdditionalTagsConfig)

    def test_explicit_params(self):
        df_parser = flexmock()
        configuration = RepoConfiguration()
        tags_config = AdditionalTagsConfig()

        repo_info = RepoInfo(df_parser, configuration, tags_config)
        assert repo_info.dockerfile_parser is df_parser
        assert repo_info.configuration is configuration
        assert repo_info.additional_tags is tags_config

    @pytest.mark.parametrize('dockerfile_missing', (False, True))
    def test_image_labels_not_flatpak(self, dockerfile_missing):
        labels = {
            'name': 'image1',
        }

        class MockParser(object):
            @property
            def labels(self):
                if dockerfile_missing:
                    raise IOError("Can't read")
                else:
                    return labels

            @property
            def baseimage(self):
                if dockerfile_missing:
                    raise IOError("Can't read")
                else:
                    return 'fedora:latest'

            @property
            def dockerfile_path(self):
                return '/foo/bar'

        repo_info = RepoInfo(MockParser())

        if dockerfile_missing:
            with pytest.raises(RuntimeError) as exc_info:
                assert repo_info.labels is None  # .labels access raises

            assert 'Could not parse Dockerfile' in str(exc_info.value)
        else:
            _, value = repo_info.labels.get_name_and_value(Labels.LABEL_TYPE_NAME)
            assert value == 'image1'
            assert repo_info.base_image == 'fedora:latest'

    @pytest.mark.parametrize('modules,name,component,expected_name,expected_component', (
        (None, None, None, None, None),
        ([], None, None, None, None),
        (['mod_name:mod_stream:mod_version'], None, None, 'mod_name', 'mod_name'),
        (['mod_name:mod_stream:mod_version', 'mod_name2:mod_stream2:mod_version2'], None, None,
         'mod_name', 'mod_name'),
        (['mod_name:mod_stream:mod_version'], 'name2', None, 'name2', 'mod_name'),
        (['mod_name:mod_stream:mod_version'], None, 'component2', 'mod_name', 'component2'),
    ))
    def test_image_labels_flatpak(self, tmpdir, modules, name, component,
                                  expected_name, expected_component):
        config_yaml = {
            'compose': {
                'modules': modules
            },
            'flatpak': {
                'id': 'org.gnome.Eog'
            }
        }
        if name:
            config_yaml['flatpak']['name'] = name
        if component:
            config_yaml['flatpak']['component'] = component

        yaml_file = tmpdir.join(REPO_CONTAINER_CONFIG)
        yaml_file.write(yaml.dump(config_yaml))

        repo_info = RepoInfo(configuration=RepoConfiguration(str(tmpdir)))

        if modules:
            _, name_label = repo_info.labels.get_name_and_value(Labels.LABEL_TYPE_NAME)
            _, component_label = repo_info.labels.get_name_and_value(Labels.LABEL_TYPE_COMPONENT)

            assert name_label == expected_name
            assert component_label == expected_component
        else:
            with pytest.raises(OsbsValidationException) as exc_info:
                assert repo_info.labels is None  # .labels access raises

            assert '"compose" config is missing "modules", required for Flatpak' in \
                   exc_info.value.message

    @pytest.mark.parametrize('base_image', (None, 'fedora:latest'))
    def test_base_image_flatpak(self, tmpdir, base_image):
        config_yaml = {
            'compose': {
                'modules': ['mod_name:mod_stream'],
            },
            'flatpak': {
                'id': 'org.gnome.Eog'
            }
        }
        if base_image:
            config_yaml['flatpak']['base_image'] = base_image

        yaml_file = tmpdir.join(REPO_CONTAINER_CONFIG)
        yaml_file.write(yaml.dump(config_yaml))

        repo_info = RepoInfo(configuration=RepoConfiguration(str(tmpdir)))

        assert repo_info.base_image == base_image


class TestRepoConfiguration(object):

    @pytest.mark.parametrize('deprecated_key, deprecated_value, deprecation_msg', [
        ('autorebuild', '', "\'autorebuild\' config is deprecated in OSBS 2.0, this config will be "
                            "ignored"),
        ('image_build_method', 'imagebuilder', "\'image_build_method\' config is deprecated in "
                                               "OSBS 2.0, this config will be ignored"),
    ])
    def test_deprecated_config(self, tmpdir, deprecated_key, deprecated_value, deprecation_msg,
                               caplog):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent(f"""\
                {deprecated_key}: {deprecated_value}
                """))
        RepoConfiguration(dir_path=str(tmpdir))
        assert deprecation_msg in caplog.text

    def test_invalid_yaml(self, tmpdir):
        yaml_file = tmpdir.join(REPO_CONTAINER_CONFIG)
        yaml_file.write('\n'.join(['hallo: 1', 'bye']))

        with pytest.raises(OsbsException) as exc_info:
            RepoConfiguration(dir_path=str(tmpdir))

        err_msg = str(exc_info.value)
        assert 'Failed to load or validate container file "{}"'.format(yaml_file) in err_msg
        assert "could not find expected ':'" in err_msg
        assert 'line 2, column 4:' in err_msg

    @pytest.mark.parametrize('module_a_nsv, module_b_nsv, should_raise', [
        ('name:stream', 'name', True),
        ('name', 'name:stream', True),
        ('name:stream:version', 'name-stream', True),
        ('name:stream:version', 'name:stream', False),
        ('name::version', 'name:stream', True),
        ('::version', 'name:stream', True),
        ('"::"', 'name:stream', True),
        ('"name:"', 'name:stream', True),
        (':version', 'name:stream', True),
        ('name', 'name::version', True),
    ])
    def test_modules_nsv_validation(self, tmpdir, module_a_nsv, module_b_nsv, should_raise):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                compose:
                    modules:
                    - %s
                    - %s
                """ % (module_a_nsv, module_b_nsv)))

        if should_raise:
            with pytest.raises(ValueError):
                conf = RepoConfiguration(dir_path=str(tmpdir))
        else:
            conf = RepoConfiguration(dir_path=str(tmpdir))
            assert conf.container['compose']['modules'][0] == module_a_nsv
            assert conf.container['compose']['modules'][1] == module_b_nsv

    @pytest.mark.parametrize('module_nsv, should_raise, expected', [
        ('name', True, None),
        ('name-stream', True, None),
        ('name-stream-version', True, None),
        ('name:stream', False, ('name', 'stream', None, None, None)),
        ('n:s:version', False, ('n', 's', 'version', None, None)),
        ('n:s:v:context', False, ('n', 's', 'v', 'context', None)),
        ('n:s:v:c/profile', False, ('n', 's', 'v', 'c', 'profile')),
        ('n:s:v/p', False, ('n', 's', 'v', None, 'p')),
        ('n:s/p', False, ('n', 's', None, None, 'p')),
        ('n/p', True, None),
    ])
    def test_container_module_specs(self, tmpdir, module_nsv, should_raise, expected):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                compose:
                    modules:
                    - %s
                """ % module_nsv))
        if should_raise:
            with pytest.raises(ValueError):
                conf = RepoConfiguration(dir_path=str(tmpdir))
        else:
            conf = RepoConfiguration(dir_path=str(tmpdir))
            assert conf.container['compose']['modules'][0] == module_nsv
            spec = conf.container_module_specs[0]
            params = module_nsv.split(':')
            assert spec.name == expected[0]
            assert spec.stream == expected[1]
            if len(params) > 2:
                assert spec.version == expected[2]
                if len(params) > 3:
                    assert spec.context == expected[3]
            assert spec.profile == expected[4]

    def test_empty_yaml_compose(self, tmpdir):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                compose:
                """))

        conf = RepoConfiguration(dir_path=str(tmpdir))
        assert conf.container['compose'] is None
        assert conf.container_module_specs == []

    def test_empty_yaml_modules(self, tmpdir):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                compose:
                    modules:
                """))

        conf = RepoConfiguration(dir_path=str(tmpdir))
        assert conf.container['compose'] == {'modules': None}
        assert conf.container_module_specs == []

    @pytest.mark.parametrize('set_base_image', (True, False))
    def test_flatpak_base_image(self, tmpdir, set_base_image):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            if set_base_image:
                f.write(dedent("""\
                    flatpak:
                        base_image: fedora:28
                    """))

        config = RepoConfiguration(dir_path=str(tmpdir))
        if set_base_image:
            assert config.flatpak_base_image == "fedora:28"
        else:
            assert config.flatpak_base_image is None

    @pytest.mark.parametrize('set_component', (True, False))
    def test_flatpak_component(self, tmpdir, set_component):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            if set_component:
                f.write(dedent("""\
                    flatpak:
                        component: mycomponent
                    """))

        config = RepoConfiguration(dir_path=str(tmpdir))
        if set_component:
            assert config.flatpak_component == "mycomponent"
        else:
            assert config.flatpak_component is None

    @pytest.mark.parametrize('set_name', (True, False))
    def test_flatpak_name(self, tmpdir, set_name):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            if set_name:
                f.write(dedent("""\
                    flatpak:
                        name: myname
                    """))

        config = RepoConfiguration(dir_path=str(tmpdir))
        if set_name:
            assert config.flatpak_name == "myname"
        else:
            assert config.flatpak_name is None

    @pytest.mark.parametrize('tag, should_fail', (
        ('latest', False),
        ('"latest"', False),
        ('"1.14"', False),
        ('1.14', True),
    ))
    def test_tags_type_validation(self, tmpdir, tag, should_fail):
        with open(os.path.join(str(tmpdir), REPO_CONTAINER_CONFIG), 'w') as f:
            f.write(dedent("""\
                tags:
                - {}
                """.format(tag)))

        if should_fail:
            match_msg = r"{} is not of type u?'string'".format(tag)
            with pytest.raises(OsbsException, match=match_msg):
                RepoConfiguration(dir_path=str(tmpdir))
        else:
            config = RepoConfiguration(dir_path=str(tmpdir))
            # "1.14" is the format written in yaml to represent a string. In
            # parsed result, it will be 1.14 without the double-quotes.
            assert [tag.replace('"', '')] == config.container['tags']

    @pytest.mark.parametrize(
        'files, error_msg',
        (
                (
                        ['container.yml'],
                        'Repo contains wrong filename: {wrong_filename}, expected: '
                        '{expected_filename}'.format(expected_filename='container.yaml',
                                                     wrong_filename='container.yml'),
                ),
                (
                        ['container.yaml', 'container.yml'],
                        'This repo contains both {expected_filename} and {wrong_filename} '
                        'Please remove {wrong_filename}'.format(
                            expected_filename='container.yaml',
                            wrong_filename='container.yml',
                        ),
                ),
                (
                        ['content_sets.yaml'],
                        'Repo contains wrong filename: {wrong_filename}, expected: '
                        '{expected_filename}'.format(expected_filename='content_sets.yml',
                                                     wrong_filename='content_sets.yaml'),
                ),
                (
                        ['content-sets.yaml'],
                        'Repo contains wrong filename: {wrong_filename}, expected: '
                        '{expected_filename}'.format(expected_filename='content_sets.yml',
                                                     wrong_filename='content-sets.yaml'),
                ),
                (
                        ['content_sets.yml', 'content-sets.yml'],
                        'This repo contains both {expected_filename} and {wrong_filename} '
                        'Please remove {wrong_filename}'.format(
                            expected_filename='content_sets.yml',
                            wrong_filename='content-sets.yml',
                        ),
                ),
                (['container.yaml'], ''),
                (['content_sets.yml'], ''),
        ),
    )
    def test_repo_files_extensions(self, tmpdir, files, error_msg):
        for repo_file in files:
            path = os.path.join(str(tmpdir), repo_file)
            os.mknod(path)
        if error_msg:
            with pytest.raises(OsbsException, match=error_msg):
                RepoConfiguration(dir_path=str(tmpdir))
        else:
            RepoConfiguration(dir_path=str(tmpdir))


class TestModuleSpec(object):
    @pytest.mark.parametrize(('as_str', 'as_str_no_profile'), [
        ('a:b', 'a:b'),
        ('a:b/p', 'a:b'),
        ('a:b:c', 'a:b:c'),
        ('a:b:c/p', 'a:b:c'),
    ])
    def test_module_spec_to_str(self, as_str, as_str_no_profile):
        spec = ModuleSpec.from_str(as_str)
        assert spec.to_str() == as_str
        assert spec.to_str(include_profile=False) == as_str_no_profile


class TestAdditionalTagsConfig(object):

    def test_default_values(self):
        conf = AdditionalTagsConfig()
        assert conf.tags == []

    def test_tags_parsed(self, tmpdir):
        tags = ['spam', 'bacon', 'eggs', 'saus.age']
        self.mock_additional_tags(str(tmpdir), tags)
        conf = AdditionalTagsConfig(dir_path=str(tmpdir))
        # Compare as a "set" because order is not guaranteed
        assert set(conf.tags) == set(tags)

    @pytest.mark.parametrize('bad_tag', [
        '{bad', 'bad}', '{bad}', 'ba-d', '-bad', 'bad-', 'b@d',
    ])
    def test_invalid_tags(self, tmpdir, bad_tag):
        tags = [bad_tag, 'good']
        self.mock_additional_tags(str(tmpdir), tags)
        conf = AdditionalTagsConfig(dir_path=str(tmpdir))
        assert conf.tags == ['good']

    def mock_additional_tags(self, dir_path, tags=None):
        contents = ''

        if tags:
            contents = '\n'.join(tags)
        with open(os.path.join(dir_path, ADDITIONAL_TAGS_FILE), 'w') as f:
            f.write(contents)
