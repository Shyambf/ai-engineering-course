# Перенос приложения Модуля 3 в Docker Compose

В рамках перехода на ветку `module-03-compose` вся программа (векторная база данных Qdrant и Python-пайплайн RAG для работы с документами, индексацией и векторным поиском) была переведена в контейнеризированную мультисервисную среду под управлением **Docker Compose**.

---

## 1. Архитектура решения

Docker Compose объединяет два сервиса в единую внутреннюю виртуальную сеть `module_03_default`:

```
+-----------------------------------------------------------------+
|                       Docker Network                            |
|                                                                 |
|   +-----------------------+           +---------------------+   |
|   |       qdrant          |<----------|        app          |   |
|   | (порт 6333:6333 хост) |  gRPC/REST| (promtech-rag-app)  |   |
|   +-----------------------+           +---------------------+   |
|               |                                  |              |
+---------------|----------------------------------|--------------+
                |                                  |
          [Том данных]                        [Том кэша] & [Биндинг]
     qdrant_storage:/qdrant/storage       rag_hf_cache:/root/.cache/hf
                                          ./data:/app/data
```

1. **`qdrant`**: Векторная база данных Qdrant v1.18.3.
   - Порт `6333` проброшен на хост для доступа к веб-интерфейсу дашборда: [http://localhost:6333/dashboard](http://localhost:6333/dashboard).
   - Том `qdrant_storage` хранит персистентные данные векторного индекса и метаданных.
2. **`app` (`promtech-rag-app`)**: Python-приложение пайплайна (парсинг документов, чанкинг, построение эмбеддингов, поиск и оценка качества).
   - Взаимодействует с базой по внутреннему DNS-имени сервиса `http://qdrant:6333`.
   - Имеет примонтированную папку `./data` с хоста, поэтому результаты генерации, манифесты и спарсенные документы синхронизируются в реальном времени.
   - Использует именованный том `rag_hf_cache` для кэширования моделей HuggingFace (`paraphrase-multilingual-MiniLM-L12-v2`), исключая повторные скачивания весов (470 МБ) при каждом запуске.

---

## 2. Что было изменено в коде

### 2.1. [`qdrant_connect.py`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/qdrant_connect.py)
* **Было:** Адрес сервера был жестко зашит как `http://localhost:6333`. При запуске внутри контейнера это приводило к ошибке подключения, так как `localhost` внутри контейнера указывает на сам контейнер приложения, а не на Qdrant.
* **Стало:**
  - Добавлено чтение хоста, порта и полного URL из переменных окружения:
    ```python
    QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")
    QDRANT_URL = os.getenv("QDRANT_URL", f"http://{QDRANT_HOST}:{QDRANT_PORT}")
    DASHBOARD_URL = os.getenv("DASHBOARD_URL", f"http://localhost:{QDRANT_PORT}/dashboard")
    ```
  - В функцию `make_client()` добавлен цикл повторных попыток подключения (`retry loop` с таймаутом до 15 секунд). Это устраняет состояние гонки (race condition), когда сервис `app` стартует одновременно с `qdrant` и база ещё проходит инициализацию.
  - Сохранена полная обратная совместимость: если скрипт запускается локально без переменных окружения, он по умолчанию обращается к `http://localhost:6333`.

### 2.2. [`docker-compose.yml`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/docker-compose.yml)
* **Было:** Описывался исключительно один сервис `qdrant`.
* **Стало:**
  - Добавлен сервис `app` с директивами:
    - `build`: сборка из локального контекста (`Dockerfile`).
    - `depends_on: [qdrant]`: контейнер приложения стартует после старта сервиса Qdrant.
    - `environment`: передача `QDRANT_HOST=qdrant`, `QDRANT_PORT=6333`, `QDRANT_URL=http://qdrant:6333`.
    - `env_file`: опциональная подгрузка файла `.env` (при наличии ключей GigaChat).
    - `volumes`:
      - `./data:/app/data` — сохранение и шаринг корпуса, манифеста и исходников.
      - `hf_cache:/root/.cache/huggingface` — сохранение весов модели трансформера.
    - `command`: запуск точки входа `["python", "entrypoint.py"]`.

### 2.3. [`Dockerfile`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/Dockerfile) *(Создан)*
* Разработан Dockerfile на базе `python:3.12-slim`:
  - Настроены переменные `PYTHONDONTWRITEBYTECODE=1` и `PYTHONUNBUFFERED=1` для корректного отображения логов в реальном времени.
  - **Оптимизация размера:** предварительная установка CPU-версии PyTorch (`--index-url https://download.pytorch.org/whl/cpu`). Это предотвращает скачивание громоздких пакетов NVIDIA CUDA (экономия более 2 ГБ трафика и диска, сборка происходит в разы быстрее).
  - Установка остальных библиотек из `requirements.txt`.
  - Копирование исходного кода проекта.

### 2.4. [`.dockerignore`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/.dockerignore) *(Создан)*
* Исключает мусорные и тяжелые файлы из контекста сборки:
  - `__pycache__`, `*.pyc`
  - `.git`, `.venv`, локальные виртуальные окружения
  - локальную embedded базу `qdrant_chunks/`

### 2.5. [`entrypoint.py`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/entrypoint.py) *(Создан)*
* «Умная» точка входа для контейнера `app`:
  - Если переданы явные аргументы (например, `python ingestion.py`), скрипт прозрачно выполняет их.
  - По умолчанию (при стандартном запуске `docker compose up`):
    1. Проверяет подключение к Qdrant.
    2. Проверяет наличие индекса в базе (коллекция/алиас `promtech_chunks`) и файла манифеста `data/index_manifest.json`.
    3. Если индекса ещё нет в базе — автоматически запускает `python ingestion.py`.
    4. Запускает поиск и расчёт метрик качества `python vector_store.py`.
  - За счёт того, что точка входа написана на Python, отсутствует проблема с несовместимостью переводов строк Windows (CRLF) и Linux (LF).

### 2.6. [`ingestion.py`](file:///c:/Users/host/Documents/ai-engineering-course/module_03/ingestion.py)
* **Было:** Функция `drop_old_collections` удаляла старые коллекции по срезу `ours[:-keep]`, где имена коллекций сортировались по дате в названии. При запуске в контейнере системное время часто работает в UTC, тогда как на хосте — UTC+3 (или локальный часовой пояс). Из-за этого только что созданная в контейнере коллекция имела меньшую временную метку, чем ранее собранные на хосте, и ошибочно удалялась самой же функцией ротации.
* **Стало:** Функция `drop_old_collections(client, keep, current=collection)` явно исключает текущую созданную коллекцию `current` из списка кандидатов на удаление, предотвращая её случайное уничтожение.

---

## 3. Инструкция по запуску и использованию

Все команды выполняются из директории `module_03`:

### 3.1. Запуск всего пайплайна одной командой
```bash
docker compose up
```
*Поднимет Qdrant, проверит наличие векторного индекса (соберет его при необходимости) и прогонит контрольные вопросы с выводом метрик качества поиска.*

### 3.2. Фоновый запуск Qdrant
Если требуется оставить Qdrant работающим в фоне:
```bash
docker compose up -d qdrant
```
Веб-интерфейс Qdrant Dashboard будет доступен по адресу:
👉 **[http://localhost:6333/dashboard](http://localhost:6333/dashboard)**

### 3.3. Запуск отдельных модулей через Compose

* **Парсинг документов (PDF/DOCX):**
  ```bash
  docker compose run --rm app python parse_documents.py
  ```

* **Сборка векторного индекса (Ingestion):**
  ```bash
  docker compose run --rm app python ingestion.py
  ```

* **Поиск и оценка качества (Vector Store):**
  ```bash
  docker compose run --rm app python vector_store.py
  ```

* **Демонстрация эмбеддингов:**
  ```bash
  docker compose run --rm app python embeddings_demo.py
  ```

### 3.4. Остановка контейнеров
```bash
docker compose down
```
*(Все данные в томах `qdrant_storage` и `rag_hf_cache`, а также локальные файлы в `./data` будут сохранены).*
