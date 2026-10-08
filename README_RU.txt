NEFOR SPOT — тест бэкапа Neon из GitHub Actions (ветка dev)

1. На GitHub откройте репозиторий:
   Settings -> Secrets and variables -> Actions -> New repository secret
   Name: NEFOR_TEST_NEON_URL
   Secret: Connection string ТОЛЬКО от ветки dev-backup из Neon (не production)
   Сохраните. Не вставляйте секрет в код, коммит или чат.

2. Убедитесь, что в GitHub Desktop выбрана ветка dev.
   Распакуйте две папки .github и scripts в корень репозитория,
   где расположен main.py; при необходимости слейте с существующими папками.

3. В GitHub Desktop: Commit to dev -> Push origin.

4. Откройте на GitHub вкладку Actions -> Neon backup test (dev).
   Проверка начнется автоматически при Push файла workflow в ветку dev.
   Зелёная галочка = архив создан и сверены контрольные суммы.
   Красный крест = ошибка; сообщите только последний этап и тип ошибки.

Этот тест работает бесплатно в рамках лимитов GitHub Actions, не запускает
Telegram-бота, не изменяет базу, НЕ загружает склад в GitHub artifacts.
Тестовый архив на сервере GitHub удаляется после проверки.

ВАЖНО: если используете Windows Explorer и не видите папку .github,
включите «Показать -> Скрытые элементы» или вставьте через проводник
полный путь .github\\workflows.
