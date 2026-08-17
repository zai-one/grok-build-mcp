# Качество тестов grok-mcp 0.10.0 — мутационный срез 2026-08-17

Метод: ручные точечные мутации в копии `$env:TEMP\grokmcp-mut` (robocopy без `.git`). Основной чекаут не правился, кроме этого файла. Прогон бил код копии: `PYTHONPATH` указывал на неё, `grok_delegate.__file__` = `...\Temp\grokmcp-mut\grok_delegate\__init__.py`.

Baseline в копии: `py -3 -m pytest tests -q` → **605 passed, 1 skipped, 79 subtests passed** (65.95 с). Один skip — `test_permission_and_artifact_readback_reject_symlink_escape` (symlink на этой Windows-машине). Вердикт «выжила» ставился только после полного набора с тем же 605/1.

Сделано **25 мутаций**, по одной, с откатом. **8 выжили, 17 убиты.**

Паттерн выживших: `permission_decision` требует `command in test_commands and _command_allowed(...)`. Тесты на злой execute почти всегда не входят в `test_commands`, поэтому внутренности `_command_allowed` не проверяются. Гейты `finalize_receipt` перезаписывают `blocked_reason` по очереди; то, что перекрывается следующим гейтом или не собрано в unit-сценарий, не ловится.

## Выжившие мутации (набор не заметил)

### F-1 Приёмка execute без доказательств тестов (`TEST_EVIDENCE_MISSING`)
- Серьёзность: blocker
- Файл: `grok_delegate/contracts.py:321`
- Мутация:
  ```
  -            if len({test["command"] for test in valid_tests}) != len(set(expected_tests)):
  -                status = "blocked"
  -                out["blocked_reason"] = "TEST_EVIDENCE_MISSING"
  +            if len({test["command"] for test in valid_tests}) != len(set(expected_tests)):
  +                pass
  ```
- Результат прогона: `605 passed, 1 skipped` — не упал никто
- Что это значит: `finalize_receipt` больше не требует, чтобы на каждую `test_commands` была запись `passed is True` / `returncode == 0` / `source == "bridge-verifier"`. Пустой `tests` или чужой source при зелёных артефактах даёт `status=completed`, `ok=True`. `TEST_FAILED` ловит только явный `passed=false`, не отсутствие прогона.
- Какой тест это закрыл бы: `finalize_receipt` с `role=execute`, совпавшими артефактами и `tests=[]` (или только `source=agent-reported`) должен стать `blocked` / `TEST_EVIDENCE_MISSING`.

### F-2 Агентский exit code принимается как verifier
- Серьёзность: blocker
- Файл: `grok_delegate/contracts.py:319`
- Мутация:
  ```
                   and test.get("passed") is True
                   and test.get("returncode") == 0
  -                and test.get("source") == "bridge-verifier"
               ]
  ```
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: запись `source: "agent-reported"` с `passed: True` закрывает гейт приёмки. В `test_fake_stdio_execute_permission_diff_and_test_evidence` есть комментарий «acceptance only counts bridge-verifier», но проверяется только вывод фейкового адаптера, не `finalize_receipt`. Живой capture как раз показал `exit_code: 0` при упавшем pytest на цепочке.
- Какой тест это закрыл бы: тот же пакет, что в F-1, с `tests=[{command, passed: True, returncode: 0, source: "agent-reported"}]` → `TEST_EVIDENCE_MISSING`, не `completed`.

### F-3 Из `_command_allowed` убраны запрещённые символы `; & | $() \n …`
- Серьёзность: blocker
- Файл: `grok_delegate/acp.py:1201`
- Мутация:
  ```
  -    if (
  -        not text
  -        or len(text) > 2_000
  -        or any(c in text for c in ("\x00", "\n", "\r", ";", "&", "|", ">", "<", "`", "(", ")"))
  -        or "$(" in text
  -    ):
  -        return False
  +    if not text or len(text) > 2_000:
  +        return False
  ```
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: цепочка в *объявленной* `test_commands` (её же исполняет bridge-verifier через `validated_test_argv`) больше не отсекается по метасимволам. `_ALLOWED_COMMAND` якорится только с начала и **без `$`**, так что `python -m pytest -q; Set-Content …` проходит префикс. Тест `test_permission_policy_is_deny_by_default` отклоняет `pytest -q; Set-Content C:\outside.txt PWN` потому что команды нет в `test_commands`, а не из-за `;`.
- Какой тест это закрыл бы: прямой вызов `_command_allowed` / `validated_test_argv` на `python -m pytest -q; echo pwned` и на `pytest -q $(Get-Content secrets.txt)` → отказ, без опоры на «команда не из списка».

### F-4 `_command_allowed` больше не держит пути внутри worktree
- Серьёзность: blocker
- Файл: `grok_delegate/acp.py:1210`
- Мутация:
  ```
  -    return bool(_ALLOWED_COMMAND.match(text)) and _command_paths_confined(text, cwd)
  +    return bool(_ALLOWED_COMMAND.match(text))
  ```
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: `pytest C:\outside\test_escape.py` как *declared* test command стала бы легальной для verifier'а. В permission-тесте та же строка отклоняется только из-за несовпадения с `test_commands`.
- Какой тест это закрыл бы: `validated_test_argv("pytest C:\\outside\\test_escape.py", cwd)` → `TEST_COMMAND_UNSAFE`; и execute-permission, где эта строка **входит** в `test_commands`, всё равно `reject-once`.

### F-5 Гейт `EXPECTED_ARTIFACT_MISSING` можно выкинуть
- Серьёзность: major
- Файл: `grok_delegate/contracts.py:290`
- Мутация: ветка `if missing: status = "blocked" / EXPECTED_ARTIFACT_MISSING` заменена на `pass`.
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: ожидаемый путь может отсутствовать в `artifacts`, и приёмка всё равно пройдёт, если путь есть в `changed_files` (тогда `EXPECTED_ARTIFACT_NOT_CHANGED` тоже молчит) и тесты зелёные. Строка `EXPECTED_ARTIFACT_MISSING` в `tests/` не встречается.
- Какой тест это закрыл бы: execute-receipt с `expected_artifacts=["expected.txt"]`, `changed_files=["expected.txt"]`, `artifacts=[]` → `blocked` / `EXPECTED_ARTIFACT_MISSING`.

### F-6 Гейт `EXPECTED_ARTIFACT_NOT_CHANGED` можно выкинуть
- Серьёзность: major
- Файл: `grok_delegate/contracts.py:297`
- Мутация: ветка `if unchanged_artifacts: … EXPECTED_ARTIFACT_NOT_CHANGED` заменена на `pass`.
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: уникальный случай — несколько expected-артефактов, часть изменена, часть нет, unexpected-файлов нет. `test_preexisting_expected_artifact_cannot_certify_unrelated_diff` сначала попал бы в этот гейт, но затем его перекрывает `UNEXPECTED_CHANGED_FILES`, поэтому удаление NOT_CHANGED тест не роняет.
- Какой тест это закрыл бы: `expected_artifacts=["a.py","b.py"]`, оба в `artifacts`, `changed_files=["a.py"]`, валидные tests → `EXPECTED_ARTIFACT_NOT_CHANGED: b.py`.

### F-7 Регексп `_FORBIDDEN_COMMAND` не проверяется сам по себе
- Серьёзность: major
- Файл: `grok_delegate/acp.py:1208`
- Мутация:
  ```
  -    if _FORBIDDEN_COMMAND.search(text):
  +    if False and _FORBIDDEN_COMMAND.search(text):
           return False
  ```
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: `git push` в permission-тесте и так не в allowlist и не в `test_commands`. Запрет `auth.json` / `curl` / `rm` внутри строки, которая уже прошла `_ALLOWED_COMMAND`, ничем не зафиксирован. Это второй слой, и он пустой с точки зрения набора.
- Какой тест это закрыл бы: `_command_allowed("python -m pytest tests/auth.json", cwd)` и `"pytest -q; curl http://evil"` (если символы `;` тестируются отдельно) → `False` именно из-за forbidden-регекспа.

### F-8 В `_verify_or_explain` переставлены `CANCELLED` и `NO_CHANGES`
- Серьёзность: minor
- Файл: `grok_delegate/agent_runtime.py:628`
- Мутация:
  ```
       if not write_role:
           return [], "NOT_A_WRITE_ROLE"
  -    if cancel_event.is_set():
  -        return [], "CANCELLED"
  -    if not changed:
  -        return [], "NO_CHANGES"
  +    if not changed:
  +        return [], "NO_CHANGES"
  +    if cancel_event.is_set():
  +        return [], "CANCELLED"
  ```
- Результат прогона: `605 passed, 1 skipped`
- Что это значит: тесты проверяют причины порознь (`changed=["a.py"]` при cancel; пустой diff без cancel). Пересечение «оператор отменил и diff пуст» теперь врёт `NO_CHANGES` вместо `CANCELLED`. Verifier в обоих случаях не бежит; опасный случай cancel *с* правками по-прежнему `CANCELLED`.
- Какой тест это закрыл бы: `_verify_or_explain(..., cancel_event=set(), write_role=True, changed=[])` → `CANCELLED`, не `NO_CHANGES`.

## Убитые мутации (коротко: что ломал и кто поймал — показывает, где набор силён)

- `commit_lane_work`: снят префикс `grok/` → `test_the_bridge_refuses_to_commit_to_anything_but_a_lane`.
- `commit_lane_work`: снята сверка `rev-parse --abbrev-ref HEAD` → `test_a_stale_branch_argument_is_not_enough_to_commit`.
- `commit_lane_work`: `commit` до `add -A` → unit на fake-git **не** поймал порядок; убил e2e `test_an_out_of_turns_job_still_commits_and_still_reports_its_tests` (живой git: нечего коммитить).
- `commit_lane_work`: убран early-return на чистое дерево → `test_a_clean_worktree_is_left_alone`.
- `_resolve_base_sha` всегда literal ref → `test_a_moving_base_is_pinned_to_a_commit`.
- `_verify_or_explain` всегда `[], None` → семь тестов в `test_lane_commit_and_verification.py`, включая e2e.
- `commit_lane_work` сдвинут **до** verifier'а → `test_verifier_cannot_revert_artifact_and_leave_stale_completed_receipt` (`completed` вместо `no_changes`).
- `_paths_confined` всегда `True` → `test_a_write_outside_the_worktree_is_still_denied`, `test_an_unknown_tool_call_is_denied_not_guessed`, `test_permission_policy_is_deny_by_default`.
- read-only профиль всегда `permitted = True` → `test_read_only_profile_rejects_the_same_write`.
- `_agent_reported_pass` на цепочке возвращает `True` → `test_a_chained_command_yields_no_verdict_at_all`.
- Снят гейт `UNEXPECTED_CHANGED_FILES` → три теста, включая `test_unexpected_changed_file_blocks_completed_execute` и e2e со stale `attacker.py`.
- `_auto_mode` всегда `operate` → пачка `test_navigator_cards_and_intent` + `test_session_protocol`.
- `_session_budget` не читает пресет → `test_navigator_defaults_follow_the_project_preset`.
- `compact_job_record` не поднимает поля из `result` → `test_compact_receipt_lifts_the_blocked_reason_out_of_result`.
- Неизвестный пресет = `standard` → `test_unknown_preset_is_rejected`.
- Битый JSON конфига = «нет конфига» → `test_broken_json_is_reported_rather_than_ignored`.
- Снят запрет `auth.json` внутри worktree в `_paths_confined` → `test_permission_policy_is_deny_by_default` (in-root auth).

## Тесты, которые не могут упасть

В `tests/` нет `assert True` как проверки сюиты: две вхождения — это *генерируемые* `test_sample.py` внутри tmp (lane-commit e2e). Пустых `@parametrize([])` и голых `except: pass` в тестах нет.

Слабые, но не мёртвые: много `assert x is not None` в jobs/MCP (`test_grok_delegate.py`, `test_jobs_durable.py`) — дальше обычно есть проверка поля, так что это не единственный ассерт.

Комментарий в `test_fake_stdio_execute_permission_diff_and_test_evidence` утверждает правило приёмки (`bridge-verifier`), которое сюита не проверяет — это F-2, не «тест, который не может упасть».

## Тихие скипы

- **`tests/test_tool_schemas.py:152-155`**: `test_validates_against_the_real_metaschema` делает `skipTest("jsonschema not installed")`. На этой машине `jsonschema 4.26.0` есть, поэтому baseline-skip не он. На машине без extra `[test]` самый жёсткий контракт схем MCP исчезает, а ручные проверки draft-04 остаются. Уже ловили; всё ещё так.
- **`tests/test_live_acp_fixtures.py:37-40` (`load`) и `:46-49` (`observed`)**: нет файла → `pytest.skip`. Гард на апгрейд CLI, который должен проигрывать живые кадры, при пропаже jsonl **молчит**, а не краснеет.
- **`tests/test_round8_bridge.py:165`**: skip, если нельзя сделать directory symlink. Это единственный skip полного набора здесь (1 skipped). На машине без symlink'ов уезжает проверка escape через junction/symlink.

## Live-acp фикстуры (удаление jsonl)

В копии переименованы все `evidence/live-acp/*.jsonl`, `.observed.json` оставлены. Команда: `py -3 -m pytest tests/test_live_acp_fixtures.py -q`.

Вывод: `4 failed, 12 passed, 17 skipped in 0.28s`.

- 17 skip — всё, что идёт через `load()` (replay кадров, permission на живых params, session/load).
- 12 passed — unit без jsonl (`_agent_reported_pass`) и тесты на `.observed.json` (protocol_version, agent_version, websocket notes).
- 4 failed — `test_fixtures_carry_no_absolute_paths_or_secrets` читает jsonl через `Path.read_text` **без** skip (`test_live_acp_fixtures.py:326`) → `FileNotFoundError`.

Итог: пропажа jsonl частично красная (редакция секретов), но **протокол-гейт на реальные кадры скиппается**. `scripts/capture_acp_live.py` ни одним тестом не исполняется — только его выходные файлы.

## Сон и скорость

Полный набор ~56–79 с (baseline 65.95 с). `time.sleep` в тестах:

- `test_fake_stdio_cancel_returns_cancelled_and_process_dies` — `time.sleep(0.2)` до `cancel.set()` (`test_round8_bridge.py:238`). Соседний `test_cancel_has_independent_grace_deadline` уже ждёт событие `session_created`; этот cancel-тест всё ещё на стене.
- `test_owned_legacy_process_obeys_cancel` — `time.sleep(0.3)` пока процесс с `time.sleep(30)` стартует (`test_round8_bridge.py:952`).
- `test_stdio_reader_applies_backpressure_for_bounded_bursts` — `time.sleep(0.25)` осознанно (комментарий: старый reader принимал burst за `ACP_OUTPUT_LIMIT`).
- Остальное — `0.01` в циклах опроса jobs.

Не находка качества ловли поломки; на полный прогон это секунды, не десятки.

## Покрытие нового кода (факты, не находки сами по себе)

| Место | Что есть |
|---|---|
| `_verify_or_explain` | Прямые тесты каждой причины порознь; пересечение cancel∩пустой diff — F-8. |
| `commit_lane_work` | Префикс, ветка, чистое дерево, `--no-verify`, e2e. Порядок add/commit ловит только живой git, не fake-git. |
| `_resolve_base_sha` | Пин SHA и fallback на literal. |
| `tests_skipped_reason` | В e2e только `is None`. |
| `lane_commit` | e2e `committed is True`. |
| `scripts/capture_acp_live.py` | Тестов скрипта нет; есть replay фикстур. |
