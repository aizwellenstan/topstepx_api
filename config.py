import os;from pathlib import Path
rootPath = Path(os.path.dirname(__file__))
import sys;sys.path.append(os.path.relpath(rootPath, os.path.dirname(__file__)))
import yaml
import logging

def load_credentials(key='discord', filepath=f'{rootPath}/config.yaml'):
    try:
        with open(filepath, 'r') as file:
            credentials = yaml.safe_load(file)
            return credentials[key]
    except Exception as e:
        logging.error("Failed to load credentials: {}".format(e))
        raise