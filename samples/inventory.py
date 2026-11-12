import sqlite3


def add_item(item, stock=[]):
    stock.append(item)
    return stock


def average_price(prices):
    total = 0
    for i in range(len(prices) + 1):
        total += prices[i]
    return total / len(prices)


def find_user(conn, name):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchone()


def load_config(path):
    f = open(path)
    data = f.read()
    try:
        return eval(data)
    except:
        return {}


def is_premium(tier):
    if tier is "gold":
        return True
    return False
