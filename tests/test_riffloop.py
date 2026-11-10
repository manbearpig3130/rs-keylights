import socket
from threading import Thread

from rs_keylights.riffloop import GameLoop, RiffLoop, parse


def test_the_mods_answers_are_read():
    assert parse("riffloop hook=1 riff=1 loop=12.500,20.250 calls=3") == GameLoop(True, True, (12.5, 20.25))
    assert parse("riffloop hook=0 riff=0 loop=- calls=0") == GameLoop(False, False, None)
    assert parse("riffloop error: not a command: dance") is None and parse("") is None


def test_commands_go_to_the_mod_and_its_state_comes_back():
    mod = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # (the mod, as the game has it)
    mod.bind(("127.0.0.1", 0))
    heard = []

    def answer():
        for _ in range(2):
            data, sender = mod.recvfrom(256)
            heard.append(data.decode())
            mod.sendto(b"riffloop hook=1 riff=1 loop=33.250,37.500 calls=1", sender)

    Thread(target=answer, daemon=True).start()
    game = RiffLoop(mod.getsockname()[1])
    assert game.loop(33.25, 37.5) == GameLoop(True, True, (33.25, 37.5))
    game.off()
    assert heard == ["loop 33.250 37.500", "off"]
    mod.close()


def test_no_game_no_answer():
    free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()  # (nothing listens there now)
    assert RiffLoop(port, wait=0.2).status() is None


def test_where_the_game_is_comes_with_its_state():
    said = parse("riffloop hook=1 riff=0 loop=- calls=0 ctl=1 t=69.321 lo=72 hi=88 held=0 speed=6 4dc=69.321")
    assert said is not None and said.time == 69.321
    assert parse("riffloop hook=1 riff=0 loop=- calls=0 ctl=0 held=0 speed=0").time is None  # (not seen yet)


def test_the_games_time_asked_for_alone():
    mod = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mod.bind(("127.0.0.1", 0))
    answers = [b"time t=13.2040 screen=LearnASong_Game arr=amonpurs_lead", b"time t=- screen=MainMenu arr=-",
               b"time t=1.5 screen=LearnASong_Game",  # (a mod that doesn't say the arrangement)
               b"riffloop error: not a command: time"]  # (a mod without it)

    def answer():
        for said in answers:
            data, sender = mod.recvfrom(256)
            assert data == b"time"
            mod.sendto(said, sender)

    Thread(target=answer, daemon=True).start()
    game = RiffLoop(mod.getsockname()[1])
    assert game.time() == (13.204, "LearnASong_Game", "amonpurs_lead")
    assert game.time() == (None, "MainMenu", None)  # (no song's playback seen)
    assert game.time() == (1.5, "LearnASong_Game", "")
    assert game.time() is None
    mod.close()
