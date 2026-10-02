#!/usr/bin/env python3
"""Тест общего почтового ящика: запись через IMAP APPEND и чтение опросом.

Телефон кладёт в ящик письмо с меткой времени, читает письма других телефонов
и считает задержку и ошибки. В конце сеанса печатает отчёт.
Запуск: python3 mailbox_test.py [минут сеанса, по умолчанию 10]
"""
import imaplib, json, os, socket, statistics, sys, time
from datetime import datetime
from email.message import EmailMessage

SERVERS = {'Яндекс': 'imap.yandex.ru', 'Gmail': 'imap.gmail.com'}
FOLDER = 'mtest'
WRITE_EVERY, POLL_EVERY = 20, 10  # секунды; опрос раз в 10–30 с по плану шага 2
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mailbox_test.json')


def ask(prompt, default=''):
    answer = input(f'{prompt}' + (f' [{default}]' if default else '') + ': ').strip()
    return answer or default


def load_config():
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cfg = json.load(f)
    if not cfg.get('device'):
        cfg['device'] = ask('Имя телефона, например nikita-iphone')
        for name in SERVERS:
            login = ask(f'{name}: адрес ящика (пусто, если не участвуете)')
            if login:
                cfg[name] = {'login': login, 'password': ask(f'{name}: пароль приложения')}
    cfg['network'] = ask('Сеть сейчас (МТС, Билайн, МегаФон, Т2, Wi-Fi)', cfg.get('network', ''))
    with open(CONFIG, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    return cfg


class Box:
    def __init__(self, name, login, password, device):
        self.name, self.login, self.password, self.device = name, login, password, device
        self.imap, self.last_uid, self.seq = None, None, 0
        self.logins = self.written = 0
        self.append_s, self.delays, self.errors = [], [], []
        self.peers = set()

    def error(self, where, e):
        self.errors.append(f'{datetime.now():%H:%M:%S} {where}: {type(e).__name__}: {e}'[:200])
        try:
            self.imap.logout()
        except Exception:
            pass
        self.imap = None

    def connect(self):
        self.logins += 1
        self.imap = imaplib.IMAP4_SSL(SERVERS[self.name], timeout=30)
        self.imap.login(self.login, self.password)
        self.imap.create(FOLDER)  # если папка уже есть, сервер вернёт NO, это не ошибка
        typ, data = self.imap.select(FOLDER)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'SELECT {FOLDER}: {data}')
        if self.last_uid is None:  # старые письма прошлых сеансов не считаем
            self.last_uid = max(self.search(1), default=0)

    def search(self, start):
        typ, data = self.imap.uid('SEARCH', None, f'UID {start}:*')
        # «N:*» всегда возвращает последнее письмо, даже если его UID меньше N
        return [u for u in map(int, data[0].split()) if u >= start]

    def write(self):
        self.seq += 1
        msg = EmailMessage()
        msg['Subject'] = f'mtest {self.device} {self.seq}'
        msg['X-Mtest'] = f'{self.device} {time.time():.3f}'
        msg.set_content('тестовое письмо, удалить после теста')
        t = time.time()
        typ, data = self.imap.append(FOLDER, None, None, msg.as_bytes())
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'APPEND: {data}')
        self.append_s.append(time.time() - t)
        self.written += 1

    def poll(self):
        self.imap.noop()
        for uid in self.search(self.last_uid + 1):
            typ, data = self.imap.uid('FETCH', str(uid), '(BODY.PEEK[HEADER.FIELDS (X-MTEST)])')
            seen = time.time()
            self.last_uid = uid
            header = data[0][1].decode(errors='replace') if data and isinstance(data[0], tuple) else ''
            parts = header.replace('X-Mtest:', '').split()
            if len(parts) == 2 and parts[0] != self.device:
                self.peers.add(parts[0])
                self.delays.append(seen - float(parts[1]))

    def step(self, do_write):
        stage = 'вход'
        try:
            if self.imap is None:
                self.connect()
            if do_write:
                stage = 'запись'
                self.write()
            stage = 'чтение'
            self.poll()
        except (imaplib.IMAP4.error, OSError, socket.timeout) as e:
            self.error(stage, e)

    def report(self):
        line = f'{self.name}: входов {self.logins}, записано {self.written}'
        if self.append_s:
            line += f' (APPEND медиана {statistics.median(self.append_s):.1f} с)'
        line += f', прочитано чужих {len(self.delays)} от {len(self.peers)} тел.'
        if self.delays:
            line += (f', задержка медиана {statistics.median(self.delays):.1f} с, '
                     f'макс {max(self.delays):.1f} с')
        line += f', ошибок {len(self.errors)}'
        return '\n'.join([line] + ['  ' + e for e in self.errors[-10:]])


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
    cfg = load_config()
    boxes = [Box(n, cfg[n]['login'], cfg[n]['password'], cfg['device']) for n in SERVERS if n in cfg]
    if not boxes:
        sys.exit(f'Нет ни одного ящика. Удалите {CONFIG} и запустите заново.')
    print(f'Сеанс {minutes:g} мин. Не закрывайте приложение. Остановить раньше: Ctrl+C.')
    start = time.time()
    last_write = start - WRITE_EVERY
    try:
        while time.time() - start < minutes * 60:
            do_write = time.time() - last_write >= WRITE_EVERY
            if do_write:
                last_write = time.time()
            for box in boxes:
                box.step(do_write)
            print('\r' + ' | '.join(f'{b.name}: записано {b.written}, прочитано {len(b.delays)}, '
                                    f'ошибок {len(b.errors)}' for b in boxes), end='', flush=True)
            time.sleep(POLL_EVERY)
    except KeyboardInterrupt:
        pass
    for box in boxes:
        if box.imap:
            try:
                box.imap.logout()
            except Exception:
                pass
    print('\n\n' + '\n'.join([
        f'Тест почтового ящика, {datetime.now():%d.%m.%Y %H:%M}',
        f'Телефон: {cfg["device"]}; сеть: {cfg["network"]}; '
        f'сеанс {(time.time() - start) / 60:.0f} мин; запись раз в {WRITE_EVERY} с, '
        f'опрос раз в {POLL_EVERY} с',
    ] + [b.report() for b in boxes]))


if __name__ == '__main__':
    main()
