import pytest
import json
import yaml # Ensure PyYAML is installed via requirements
import os
from utils import config_loader

# Fixture to create a temporary JSON config file
@pytest.fixture
def temp_json_config(tmp_path):
    config_data = {"key": "value", "nested": {"n_key": "n_value"}}
    file_path = tmp_path / "test_config.json"
    with open(file_path, 'w') as f:
        json.dump(config_data, f)
    return file_path, config_data

# Fixture to create a temporary YAML config file
@pytest.fixture
def temp_yaml_config(tmp_path):
    config_data = {"key": "yaml_value", "nested_yaml": {"ny_key": "ny_value"}}
    file_path = tmp_path / "test_config.yaml"
    with open(file_path, 'w') as f:
        yaml.dump(config_data, f)
    return file_path, config_data

# Fixture to create a temporary unsupported format file
@pytest.fixture
def temp_unsupported_config(tmp_path):
    file_path = tmp_path / "test_config.txt"
    with open(file_path, 'w') as f:
        f.write("This is plain text.")
    return file_path

def test_can_import_config_loader():
    assert config_loader is not None

def test_load_json_config_successfully(temp_json_config):
    file_path, expected_data = temp_json_config
    loaded_data = config_loader.load_config(file_path)
    assert loaded_data == expected_data

def test_load_yaml_config_successfully(temp_yaml_config):
    file_path, expected_data = temp_yaml_config
    loaded_data = config_loader.load_config(file_path)
    assert loaded_data == expected_data

def test_load_yml_config_successfully(tmp_path): # Test .yml extension
    config_data = {"key": "yml_value"}
    file_path = tmp_path / "test_config.yml"
    with open(file_path, 'w') as f:
        yaml.dump(config_data, f)
    loaded_data = config_loader.load_config(file_path)
    assert loaded_data == config_data

def test_load_config_file_not_found():
    with pytest.raises(FileNotFoundError, match="Configuration file not found: non_existent_config.json"):
        config_loader.load_config("non_existent_config.json")

def test_load_config_unsupported_format(temp_unsupported_config):
    file_path = temp_unsupported_config
    with pytest.raises(ValueError, match="Unsupported configuration file format: .txt"):
        config_loader.load_config(file_path)

def test_load_config_corrupted_json(tmp_path):
    file_path = tmp_path / "corrupted.json"
    with open(file_path, 'w') as f:
        f.write('{"key": "value",') # Missing closing brace
    # The custom error message "Error loading configuration file..." is printed, then original exception raised
    with pytest.raises(json.JSONDecodeError):
        config_loader.load_config(file_path)

def test_load_config_corrupted_yaml(tmp_path):
    file_path = tmp_path / "corrupted.yaml"
    with open(file_path, 'w') as f:
        f.write('key: value\n  nested: value_without_proper_indent')
    with pytest.raises(yaml.YAMLError):
        config_loader.load_config(file_path)

def test_load_empty_json_file(tmp_path):
    file_path = tmp_path / "empty.json"
    with open(file_path, 'w') as f:
        f.write('')
    with pytest.raises(json.JSONDecodeError):
        config_loader.load_config(file_path)

def test_load_empty_yaml_file(tmp_path):
    file_path = tmp_path / "empty.yaml"
    with open(file_path, 'w') as f:
        f.write('')
    assert config_loader.load_config(file_path) is None
