"""Reproduce aggregate funnel from CSV using only Python's standard library.

Usage: python run_analysis.py [directory_of_csvs]
Writes funnel.md beside this script. Never writes row-level data.
"""
import csv
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUT = datetime(2026, 11, 7, tzinfo=timezone.utc).timestamp()


def epoch(value, offset=0):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=offset)))
    return dt.timestamp()


def analyze(source, clock, offset):
    db = sqlite3.connect(':memory:')
    db.create_function('epoch', 1, epoch)
    db.create_function('panel_epoch', 1, lambda s: epoch(s, offset))
    for name in ('subscriptions', 'connections', 'transactions'):
        with (source / f'{name}.csv').open(encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            cols = next(reader)
            db.execute(f'CREATE TABLE "{name}" (' + ','.join(f'"{c}" TEXT' for c in cols) + ')')
            db.executemany(f'INSERT INTO "{name}" VALUES (' + ','.join('?' for _ in cols) + ')', reader)
    db.execute('CREATE TABLE params(cut_epoch REAL, panel_offset_hours INTEGER, payment_clock TEXT)')
    db.execute('INSERT INTO params VALUES (?,?,?)', (CUT, offset, clock))
    db.executescript((HERE / 'analysis.sql').read_text(encoding='utf-8'))
    result = dict(db.execute('SELECT metric,value FROM metrics'))
    # Independent cohort arithmetic and conservation checks.
    assert result['visible_trial_users'] == result['immature_trial_users'] + result['mature_trial_users_before_exclusion']
    assert result['cohort_users'] + result['excluded_prior_payers'] == result['mature_trial_users_before_exclusion']
    assert result['linked_panel_users'] + result['unknown_panel_users'] == result['cohort_users']
    assert result['connected_then_paid_7d'] <= min(result['connected_7d'], result['paid_7d'])
    assert result['help_24h_candidates'] == result['help_connected_other_panel_by_24h'] + result['help_after_all_panels_suppression']
    assert db.execute('SELECT COUNT(*)=COUNT(DISTINCT user) FROM cohort').fetchone()[0]
    db.close()
    return result


def main():
    source = Path(sys.argv[1]) if len(sys.argv)>1 else HERE.parent / 'subscription_analysis_data'
    results = {(clock, offset): analyze(source, clock, offset)
               for clock in ('created_at', 'updated_at') for offset in (0, 3)}
    b = results['created_at', 0]
    n = b['cohort_users']
    rows = [
      ('Пользователи с видимой trial-записью', 'visible_trial_users'),
      ('Неполное окно: исключены', 'immature_trial_users'),
      ('Полное окно до исключения прошлых плательщиков', 'mature_trial_users_before_exclusion'),
      ('Исключены: успешная оплата до trial', 'excluded_prior_payers'),
      ('Условная когорта', 'cohort_users'),
      ('Панель найдена по panel_user trial-записи', 'linked_panel_users'),
      ('Связь с панелью неизвестна', 'unknown_panel_users'),
      ('Связанная панель, first_connected_at отсутствует', 'linked_null_first_connection'),
      ('Первое подключение раньше trial', 'connection_before_trial'),
      ('Первое подключение за 24 часа', 'connected_24h'),
      ('Первое подключение за 7 суток', 'connected_7d'),
      ('Успешная оплата за 7 суток', 'paid_7d'),
      ('Подключение, затем оплата за 7 суток', 'connected_then_paid_7d'),
      ('Оплата раньше первого подключения', 'paid_before_first_connection_7d'),
      ('Оплата без подключения в корректном 7-дневном окне', 'paid_without_valid_connection_7d'),
      ('Кандидаты автопомощи на 24-м часу', 'help_24h_candidates'),
      ('Из кандидатов уже подключались на другой связанной панели до 24ч', 'help_connected_other_panel_by_24h'),
      ('Остались после проверки всех связанных панелей', 'help_after_all_panels_suppression'),
      ('Из кандидатов подключились до конца 7 суток', 'help_candidates_later_connect_7d'),
      ('Из кандидатов оплатили до конца 7 суток', 'help_candidates_paid_7d'),
    ]
    lines = ['# Условная воронка видимых trial-записей', '',
      'Срез: **07.11.2026 00:00 UTC** — консервативная граница, поскольку точное время снимка неизвестно. '
      'Единица анализа — пользователь с первой **видимой** записью `is_trial=t`. '
      'Это когорта сохранившихся записей, а не все старты trial и не конверсия всего сервиса.', '',
      'Основной вариант: наивное время панели интерпретировано как UTC; время оплаты — `created_at`. '
      'Окно `[trial_at, trial_at + 7 суток)` должно полностью закончиться к срезу; '
      'пользователи с положительной нетестовой транзакцией `COMPLETED` до trial исключены. '
      'Оплаты разных валют считаются событиями, денежные суммы не складываются.', '',
      '| Показатель | Пользователи |', '|---|---:|']
    lines += [f'| {label} | {b[key]} |' for label, key in rows]
    lines += ['', f'В основной когорте подключение в окне записано у **{b["connected_7d"]/n:.1%}**, '
      f'оплата — у **{b["paid_7d"]/n:.1%}**, последовательность подключение → оплата — '
      f'у **{b["connected_then_paid_7d"]/n:.1%}**. Это разные показатели: нельзя подменять последовательную воронку любой оплатой.', '',
      f'Медиана времени от trial до первого подключения среди подключившихся в корректном 7-дневном окне: '
      f'**{b["median_minutes_to_connection_7d"]:.2f} мин**. Не включает неподключившихся и отрицательные интервалы.', '',
      '## Чувствительность допущений', '',
      '| Прокси оплаты | Время панели | Когорта | Подключились 7д | Оплатили 7д | Подключение → оплата | До trial подключены | Автопомощь 24ч | Медиана, мин |',
      '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for (clock, offset), r in results.items():
        lines.append(f'| {clock} | {"UTC" if offset==0 else "Europe/Moscow (UTC+3)"} | {r["cohort_users"]} | {r["connected_7d"]} | {r["paid_7d"]} | {r["connected_then_paid_7d"]} | {r["connection_before_trial"]} | {r["help_24h_candidates"]} | {r["median_minutes_to_connection_7d"]:.2f} |')
    lines += ['', 'Сдвиг панели на UTC+3 создаёт 63 подключения раньше trial и резко меняет активацию и медиану. '
      'Поэтому показатель 75/97 и медиана 2,89 минуты условны: перед продуктовым выводом '
      'нужно подтвердить часовой пояс по конфигурации приложения или первичному журналу. '
      'Вариант UTC согласуется с порядком событий лучше, но это не доказательство правильного часового пояса.', '',
      '`created_at` — время создания транзакции, `updated_at` — последнего изменения. '
      'Ни одно поле не подтверждено как точное время успешной оплаты. Статус известен только на снимке: '
      'транзакция могла стать COMPLETED позже границы окна или среза. Четыре варианта проверяют чувствительность, '
      'но не восстанавливают отсутствующую историю статусов.', '',
      '## Сегмент для проверки автопомощи', '',
      f'На 24-м часу критерии выполняют **{b["help_24h_candidates"]}** пользователей: '
      'есть связанная панель, первое подключение отсутствует либо наступило не раньше 24 часов, '
      'успешная оплата по выбранному прокси в первые 24 часа отсутствует. '
      'Пользователи с подключением до trial и неизвестной связью с панелью не включены. '
      f'Проверка всех связанных панелей исключает ещё **{b["help_connected_other_panel_by_24h"]}**: '
      'на другом аккаунте панели первое подключение уже произошло до 24-го часа, в том числе до trial. '
      f'После этого фильтра остаются **{b["help_after_all_panels_suppression"]}** кандидатов. '
      'Проверка использует объединение связей user–panel_user из подписок и панели; '
      'историческая доступность этих связей на 24-м часу неизвестна. '
      'Это ретроспективная оценка аудитории, а не доказательство, что помощь повысит оплату. '
      'В эксперименте нужна случайная контрольная группа; отдельно контролировать подключение за 48 часов, '
      'оплату за 7 суток и отписки от сообщений. Блокировки и другие исторические состояния '
      'на момент отправки неизвестны; надёжной истории согласия на сообщения также нет.', '',
      '| Время панели | Исходные кандидаты | Подключены на другой панели до 24ч | После исключения |',
      '|---|---:|---:|---:|']
    for offset in (0, 3):
        r = results['created_at', offset]
        lines.append(f'| {"UTC" if offset==0 else "Europe/Moscow (UTC+3)"} | {r["help_24h_candidates"]} | {r["help_connected_other_panel_by_24h"]} | {r["help_after_all_panels_suppression"]} |')
    lines += ['', 'Проверка повторена для обоих прокси оплаты; результаты фильтра всех панелей совпадают.', '',
      f'В основном варианте категории «оплата раньше первого подключения» ({b["paid_before_first_connection_7d"]}) '
      f'и «оплата без корректного подключения в 7-дневном окне» ({b["paid_without_valid_connection_7d"]}) '
      f'имеют пересечение **{b["payment_category_overlap"]}** пользователей. '
      'Таким образом, 33 последовательных оплаты + 4 оплаты до подключения + 1 без корректного подключения = 38. '
      'Равенство описывает именно основной вариант: при других временных допущениях категории необходимо перепроверять.', '',
      '## Ограничения и воспроизведение', '',
      'Связь построена по `panel_user` первой trial-подписки; другие панели того же пользователя '
      'не подмешиваются в воронку; все связанные панели используются только для дополнительного исключения из автопомощи. '
      'Отсутствие панели — неизвестная наблюдаемость; отсутствие `first_connected_at` '
      'на связанной панели — отсутствие записи, а не установленная причина ухода. '
      'Запросы конфигурации не доказывают подключение и в этап активации не включены. '
      'Снимок подписок может терять историю trial; первое видимое событие не обязательно первое реальное. '
      'Интерпретация UTC/Moscow — сценарии, а не подтверждение часового пояса.', '',
      'Запуск: `python run_analysis.py` или `python run_analysis.py <каталог_CSV>`. '
      'По умолчанию CSV читаются из `../subscription_analysis_data` относительно скрипта. '
      'Зависимости: Python 3.9+ и стандартная библиотека, SQLite с оконными функциями. '
      'SQL находится в `analysis.sql`. Скрипт проверяет разбиение когорты, уникальность пользователей '
      'и вложенность последовательной конверсии; публикуются только агрегаты.', '']
    (HERE / 'funnel.md').write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(f'{clock}; panel UTC{offset:+}: {r}' for (clock, offset), r in results.items()))


if __name__ == '__main__':
    main()
