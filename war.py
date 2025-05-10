"""
war card game client and server
"""
import asyncio
from collections import namedtuple
from enum import Enum
import logging
import random
import socket
import threading
import sys

"""
Namedtuples work like classes, but are much more lightweight so they end
up being faster. It would be a good idea to keep objects in each of these
for each game which contain the game's state, for instance things like the
socket, the cards given, the cards still available, etc.
"""
Game = namedtuple("Game", ["p1", "p2"])

# Stores the clients waiting to get connected to other clients
waiting_clients = []
wait_lock = threading.Lock()

class Command(Enum):
    """
    The byte values sent as the first byte of any message in the war protocol.
    """
    WANTGAME = 0
    GAMESTART = 1
    PLAYCARD = 2
    PLAYRESULT = 3


class Result(Enum):
    """
    The byte values sent as the payload byte of a PLAYRESULT message.
    """
    WIN = 0
    DRAW = 1
    LOSE = 2

def readexactly(sock, numbytes):
    """
    Accumulate exactly `numbytes` from `sock` and return those. If EOF is found
    before numbytes have been received, be sure to account for that here or in
    the caller.
    """
    # TODO
    data = b''
    while len(data) < numbytes:
        chunk = sock.recv(numbytes - len(data))
        if not chunk:
            raise EOFError("Connection closed")
        data += chunk
    return data


def kill_game(game):
    """
    TODO: If either client sends a bad message, immediately nuke the game.
    """
    for s in (game.p1, game.p2):
        try:
            s.close()
        except Exception:
            pass


def compare_cards(card1, card2):
    """
    TODO: Given an integer card representation, return -1 for card1 < card2,
    0 for card1 = card2, and 1 for card1 > card2
    """
    r1 = card1 % 13
    r2 = card2 % 13
    if r1 < r2:
        return -1
    if r1 > r2:
        return 1
    return 0

def deal_cards():
    """
    TODO: Randomize a deck of cards (list of ints 0..51), and return two
    26 card "hands."
    """
    deck = list(range(52))
    random.shuffle(deck)
    return deck[:26], deck[26:]

def handle_game(game):
    """
    Play one full 26‐round game between game.p1 and game.p2.
    """
    p1, p2 = game.p1, game.p2
    try:
        # 1) both must send WANTGAME(0) with payload 0
        m1 = readexactly(p1, 2)
        m2 = readexactly(p2, 2)
        if (m1[0] != Command.WANTGAME.value or m1[1] != 0 or
                m2[0] != Command.WANTGAME.value or m2[1] != 0):
            logging.error("Bad WANTGAME from one client")
            kill_game(game)
            return

        # 2) deal and send GAMESTART
        hand1, hand2 = deal_cards()
        p1.sendall(bytes([Command.GAMESTART.value]) + bytes(hand1))
        p2.sendall(bytes([Command.GAMESTART.value]) + bytes(hand2))

        used1, used2 = set(), set()

        # 3) 26 rounds
        for _ in range(26):
            cmsg1 = readexactly(p1, 2)
            cmsg2 = readexactly(p2, 2)
            if cmsg1[0] != Command.PLAYCARD.value or cmsg2[0] != Command.PLAYCARD.value:
                logging.error("Expected PLAYCARD")
                kill_game(game)
                return

            c1, c2 = cmsg1[1], cmsg2[1]
            # validate card ownership and no duplicates
            if c1 not in hand1 or c1 in used1 or c2 not in hand2 or c2 in used2:
                logging.error("Invalid or duplicate card")
                kill_game(game)
                return
            used1.add(c1)
            used2.add(c2)

            cmp = compare_cards(c1, c2)
            if cmp > 0:
                r1, r2 = Result.WIN.value, Result.LOSE.value
            elif cmp < 0:
                r1, r2 = Result.LOSE.value, Result.WIN.value
            else:
                r1 = r2 = Result.DRAW.value

            p1.sendall(bytes([Command.PLAYRESULT.value, r1]))
            p2.sendall(bytes([Command.PLAYRESULT.value, r2]))
        # 4) clean shutdown
        p1.close()
        p2.close()

    except (EOFError, ConnectionResetError, OSError) as e:
        logging.error(f"Game aborted: {e}")
        kill_game(game)

def serve_game(host, port):
    """
    TODO: Open a socket for listening for new connections on host:port, and
    perform the war protocol to serve a game of war between each client.
    This function should run forever, continually serving clients.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen()
    logging.info(f"Serving WAR on {host}:{port}")

    while True:
        client_sock, addr = listener.accept()
        logging.info(f"Client from {addr}")
        with wait_lock:
            waiting_clients.append(client_sock)
            # once we have two, pop them off and start a game thread
            if len(waiting_clients) >= 2:
                p1 = waiting_clients.pop(0)
                p2 = waiting_clients.pop(0)
                game = Game(p1=p1, p2=p2)
                t = threading.Thread(target=handle_game, args=(game,), daemon=True)
                t.start()

async def limit_client(host, port, loop, sem):
    """
    Limit the number of clients currently executing.
    You do not need to change this function.
    """
    async with sem:
        return await client(host, port, loop)
async def client(host, port, loop):
    """
    Run an individual client on a given event loop.
    You do not need to change this function.
    """
    try:
        reader, writer = await asyncio.open_connection(host, port)
        # send want game
        writer.write(b"\0\0")
        card_msg = await reader.readexactly(27)
        myscore = 0
        for card in card_msg[1:]:
            writer.write(bytes([Command.PLAYCARD.value, card]))
            result = await reader.readexactly(2)
            if result[1] == Result.WIN.value:
                myscore += 1
            elif result[1] == Result.LOSE.value:
                myscore -= 1
        if myscore > 0:
            outcome = "won"
        elif myscore < 0:
            outcome = "lost"
        else:
            outcome = "drew"
        logging.debug("Game complete, I %s", outcome)
        writer.close()
        return 1
    except ConnectionResetError:
        logging.error("ConnectionResetError")
        return 0
    except asyncio.streams.IncompleteReadError:
        logging.error("asyncio.streams.IncompleteReadError")
        return 0
    except OSError:
        logging.error("OSError")
        return 0


def main(args):
    """
    launch a client/server
    """
    host = args[1]
    port = int(args[2])
    if args[0] == "server":
        try:
            serve_game(host, port)
        except KeyboardInterrupt:
            pass
        return
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if args[0] == "client":
        loop.run_until_complete(client(host, port, loop))
    elif args[0] == "clients":
        sem = asyncio.Semaphore(1000)
        num_clients = int(args[3])
        clients = [limit_client(host, port, loop, sem)
                   for _ in range(num_clients)]

        async def run_all_clients():
            """
            use `as_completed` to spawn all clients simultaneously
            and collect their results in arbitrary order.
            """
            completed = 0
            for client_result in asyncio.as_completed(clients):
                completed += await client_result
            return completed

        res = loop.run_until_complete(
            asyncio.Task(run_all_clients(), loop=loop))
        logging.info("%d completed clients", res)

    loop.close()


if __name__ == "__main__":
    # Changing logging to DEBUG
    logging.basicConfig(level=logging.DEBUG)
    main(sys.argv[1:])
