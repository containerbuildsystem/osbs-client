"""
Copyright (c) 2020 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""


from __future__ import absolute_import, unicode_literals

from pkg_resources import resource_stream

import codecs
import json
import jsonschema
import logging
import yaml


logger = logging.getLogger(__name__)


def read_yaml_from_file_path(file_path, schema):
    """
    :param yaml_data: string, yaml content
    :param schema: string, file path to the JSON schema
    """
    with open(file_path) as f:
        yaml_data = f.read()
    return read_yaml(yaml_data, schema)


def read_yaml(yaml_data, schema):
    """
    :param yaml_data: string, yaml content
    :param schema: string, file path to the JSON schema
    """
    try:
        resource = resource_stream('osbs', schema)
        schema = codecs.getreader('utf-8')(resource)
    except (IOError, TypeError):
        logger.error('unable to extract JSON schema, cannot validate')
        raise

    try:
        schema = json.load(schema)
    except ValueError:
        logger.error('unable to decode JSON schema, cannot validate')
        raise
    data = yaml.safe_load(yaml_data)
    validator = jsonschema.Draft4Validator(schema=schema)
    try:
        jsonschema.Draft4Validator.check_schema(schema)
        validator.validate(data)
    except jsonschema.SchemaError:
        logger.error('invalid schema, cannot validate')
        raise
    except jsonschema.ValidationError:
        for error in validator.iter_errors(data):
            path = "".join(
                ('[{}]' if isinstance(element, int) else '.{}').format(element)
                for element in error.path
            )

            if path.startswith('.'):
                path = path[1:]

            logger.error('validation error (%s): %s', path or 'at top level', error.message)
        raise

    return data
