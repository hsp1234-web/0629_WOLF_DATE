import json
import yaml # Added PyYAML to requirements.txt
import os

def load_config(config_path):
    """
    Loads a configuration file. Supports JSON and YAML formats.
    Determines the format based on the file extension.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    _, file_extension = os.path.splitext(config_path)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            if file_extension.lower() == '.json':
                return json.load(f)
            elif file_extension.lower() in ['.yaml', '.yml']:
                return yaml.safe_load(f)
            else:
                raise ValueError(f"Unsupported configuration file format: {file_extension}. Supported formats are JSON and YAML.")
    except Exception as e:
        print(f"Error loading configuration file {config_path}: {e}")
        raise

if __name__ == '__main__':
    # Create dummy files for testing
    dummy_json_path = 'dummy_config.json'
    dummy_yaml_path = 'dummy_config.yaml'
    dummy_txt_path = 'dummy_config.txt'

    with open(dummy_json_path, 'w') as f:
        json.dump({"key_json": "value_json", "nested": {"n_key": "n_value"}}, f)

    with open(dummy_yaml_path, 'w') as f:
        yaml.dump({"key_yaml": "value_yaml", "nested_yaml": {"ny_key": "ny_value"}}, f)

    with open(dummy_txt_path, 'w') as f:
        f.write("this is not a config file")

    print(f"Loading {dummy_json_path}...")
    config_data_json = load_config(dummy_json_path)
    print(config_data_json)

    print(f"Loading {dummy_yaml_path}...")
    config_data_yaml = load_config(dummy_yaml_path)
    print(config_data_yaml)

    try:
        print(f"Loading non_existent_config.json...")
        load_config('non_existent_config.json')
    except FileNotFoundError as e:
        print(e)

    try:
        print(f"Loading {dummy_txt_path}...")
        load_config(dummy_txt_path)
    except ValueError as e:
        print(e)

    os.remove(dummy_json_path)
    os.remove(dummy_yaml_path)
    os.remove(dummy_txt_path)
