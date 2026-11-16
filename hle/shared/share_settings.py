from pathlib import Path
import os
ROOT = Path(os.environ.get('O1_EVAL_ROOT', Path(__file__).resolve().parents[1] / 'runtime')).resolve()
MAIN_PATH = os.environ['O1_MAIN_MODEL_PATH']
SUB_PATH = os.environ['O1_SUB_MODEL_PATH']
MAIN_NAME = 'Orchestra-o1-8B-all-ourdata'
SUB_NAME = 'Qwen3-Omni-30B-A3B-Instruct'
MAIN_URL = os.environ.get('O1_MAIN_URL', 'http://127.0.0.1:18008/v1')
SUB_URL = os.environ.get('O1_SUB_URL', 'http://127.0.0.1:8802/v1')
LOCAL_KEY = os.environ.get('O1_LOCAL_API_KEY', 'EMPTY')
TOOL_IMAGE = os.environ.get('O1_TOOL_IMAGE', 'o1-eval-tools:share')
