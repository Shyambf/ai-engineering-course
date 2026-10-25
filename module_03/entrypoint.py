"""
Entrypoint для запуска приложения внутри Docker Compose.
"""
import json
import sys
import subprocess
from pathlib import Path
from qdrant_connect import make_client

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "qdrant_chunks"
MANIFEST_PATH = BASE_DIR / "data" / "index_manifest.json"


def main():
    if len(sys.argv) > 1:
        # Если передана явная команда, выполняем её
        sys.exit(subprocess.call(sys.argv[1:]))

    print("=== [promtech-rag-app] Проверка подключения к Qdrant ===")
    client = make_client(False, DB_PATH)
    try:
        aliases = [a.alias_name for a in client.get_aliases().aliases]
    finally:
        client.close()

    manifest = None
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            manifest = None

    target_alias = manifest.get("alias", "promtech") if manifest else "promtech"

    if not manifest or target_alias not in aliases:
        print(f"Алиас '{target_alias}' не найден в Qdrant. Запуск сборки индекса...")
        print("\n=== [promtech-rag-app] Построение векторного индекса (ingestion.py) ===")
        res = subprocess.call([sys.executable, "ingestion.py"])
        if res != 0:
            print("Ошибка при выполнении ingestion.py")
            sys.exit(res)

    print("\n=== [promtech-rag-app] Запуск поиска и оценки качества (vector_store.py) ===")
    sys.exit(subprocess.call([sys.executable, "vector_store.py"]))


if __name__ == "__main__":
    main()
