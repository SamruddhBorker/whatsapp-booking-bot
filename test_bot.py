import hashlib
import hmac
import json
import threading
import unittest
import urllib.request
from datetime import date, datetime, time
from http.server import ThreadingHTTPServer
from pathlib import Path

import os

import engine
import reminders
import server
import whatsapp
from engine import Bot, parse_date, parse_time
from storage import Store

CFG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 18, 10, 0)  # a Wednesday, 10:00
TODAY = NOW.date()


def make(cfg=None):
    alerts = []
    bot = Bot(cfg or dict(CFG), Store(), clock=lambda: NOW, notify=alerts.append)
    return bot, alerts


def book(bot, phone="911", day="tomorrow", t="7pm", party="4", name="Sam"):
    for msg in ("book", day, t, party, name, "yes"):
        last = bot.handle(phone, msg)
    return last


class ParsingTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_date("today", TODAY), TODAY)
        self.assertEqual(parse_date("Tomorrow", TODAY), date(2026, 11, 19))
        self.assertEqual(parse_date("saturday", TODAY), date(2026, 11, 21))
        self.assertEqual(parse_date("wednesday", TODAY), TODAY)
        self.assertEqual(parse_date("25 nov", TODAY), date(2026, 11, 25))
        self.assertEqual(parse_date("Nov 25", TODAY), date(2026, 11, 25))
        self.assertEqual(parse_date("25/11", TODAY), date(2026, 11, 25))
        self.assertEqual(parse_date("2026-12-01", TODAY), date(2026, 12, 1))
        self.assertEqual(parse_date("5 jan", TODAY), date(2027, 1, 5))  # rolls to next year
        self.assertIsNone(parse_date("blah", TODAY))
        self.assertIsNone(parse_date("31 feb", TODAY))

    def test_times(self):
        self.assertEqual(parse_time("7pm"), time(19, 0))
        self.assertEqual(parse_time("7:30 PM"), time(19, 30))
        self.assertEqual(parse_time("19:00"), time(19, 0))
        self.assertEqual(parse_time("7"), time(19, 0))
        self.assertEqual(parse_time("10am"), time(10, 0))
        self.assertEqual(parse_time("12pm"), time(12, 0))
        self.assertEqual(parse_time("12am"), time(0, 0))
        self.assertIsNone(parse_time("25:00"))
        self.assertIsNone(parse_time("7:75"))
        self.assertIsNone(parse_time("soon"))


class FlowTests(unittest.TestCase):
    def test_full_booking_and_owner_alert(self):
        bot, alerts = make()
        reply = book(bot)
        self.assertIn("Booked", reply)
        self.assertIn("B0001", reply)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Sam", alerts[0])
        self.assertIn("B0001", bot.handle("911", "my booking"))

    def test_help_and_faq(self):
        bot, _ = make()
        self.assertIn("BOOK", bot.handle("1", "hello"))
        self.assertIn("Wi-Fi", bot.handle("1", "do you have wifi?"))
        self.assertIn("09:00 AM".lstrip("0"), bot.handle("1", "what are your hours"))

    def test_closed_day_rejected(self):
        cfg = dict(CFG, closed_days=["thursday"])
        bot, _ = make(cfg)
        bot.handle("1", "book")
        self.assertIn("closed", bot.handle("1", "tomorrow").lower())

    def test_past_and_far_dates_rejected(self):
        bot, _ = make()
        bot.handle("1", "book")
        self.assertIn("passed", bot.handle("1", "2026-11-01"))
        self.assertIn("days ahead", bot.handle("1", "2027-06-01"))

    def test_time_rules(self):
        bot, _ = make()
        bot.handle("1", "book")
        bot.handle("1", "tomorrow")
        self.assertIn("open at", bot.handle("1", "8am"))
        self.assertIn("last booking", bot.handle("1", "10pm").lower())
        self.assertIn("30-minute", bot.handle("1", "7:10pm"))

    def test_same_day_past_time_rejected(self):
        bot, _ = make()
        bot.handle("1", "book")
        bot.handle("1", "today")
        self.assertIn("passed", bot.handle("1", "10:00 am"))

    def test_capacity_and_alternatives(self):
        cfg = dict(CFG, tables_per_slot=2, max_active_bookings=5)
        bot, _ = make(cfg)
        book(bot, "A")
        book(bot, "B")
        bot.handle("C", "book")
        bot.handle("C", "tomorrow")
        reply = bot.handle("C", "7pm")
        self.assertIn("fully booked", reply)
        self.assertIn("6:30 PM", reply)

    def test_party_too_big(self):
        bot, _ = make()
        for m in ("book", "tomorrow", "7pm"):
            bot.handle("1", m)
        self.assertIn("call us", bot.handle("1", "20"))

    def test_active_booking_limit(self):
        cfg = dict(CFG, max_active_bookings=1)
        bot, _ = make(cfg)
        book(bot, "1")
        self.assertIn("maximum", bot.handle("1", "book"))

    def test_cancel_flow_single(self):
        bot, alerts = make()
        book(bot)
        self.assertIn("Cancel this booking", bot.handle("911", "cancel"))
        self.assertIn("Cancelled B0001", bot.handle("911", "yes"))
        self.assertIn("no upcoming", bot.handle("911", "my booking"))
        self.assertTrue(any("cancelled" in a for a in alerts))

    def test_cancel_flow_pick(self):
        bot, _ = make()
        book(bot, "1", day="tomorrow")
        book(bot, "1", day="friday")
        reply = bot.handle("1", "cancel")
        self.assertIn("1.", reply)
        self.assertIn("2.", reply)
        bot.handle("1", "2")
        self.assertIn("Cancelled B0002", bot.handle("1", "yes"))

    def test_cannot_cancel_someone_elses_booking(self):
        bot, _ = make()
        book(bot, "A")
        self.assertFalse(bot.store.cancel(1, "B"))

    def test_reset_words_abort_flow(self):
        bot, _ = make()
        bot.handle("1", "book")
        self.assertIn("stopped", bot.handle("1", "menu"))
        self.assertEqual(bot.store.get_session("1")[0], "idle")

    def test_name_is_sanitised(self):
        bot, _ = make()
        for m in ("book", "tomorrow", "7pm", "2"):
            bot.handle("1", m)
        reply = bot.handle("1", "<script>alert(1)</script> Sam")
        self.assertNotIn("<", reply)

    def test_race_on_last_slot(self):
        cfg = dict(CFG, tables_per_slot=1, max_active_bookings=5)
        bot, _ = make(cfg)
        for m in ("book", "tomorrow", "7pm", "2", "Late"):
            bot.handle("late", m)
        book(bot, "early")  # takes the last table while 'late' is at the confirm step
        reply = bot.handle("late", "yes")
        self.assertIn("just taken", reply)


class ReminderTests(unittest.TestCase):
    def test_reminders_for_tomorrow_only_once(self):
        bot, _ = make()
        book(bot, "1", day="tomorrow")
        book(bot, "2", day="friday")
        items = reminders.build_reminders(bot.store, CFG, TODAY)
        self.assertEqual(len(items), 1)
        self.assertIn("tomorrow", items[0][2])
        bot.store.mark_reminded(items[0][0])
        self.assertEqual(reminders.build_reminders(bot.store, CFG, TODAY), [])


class WebhookTests(unittest.TestCase):
    def setUp(self):
        os.environ["VERIFY_TOKEN"] = "tok"
        os.environ["APP_SECRET"] = "s3cret"
        os.environ.pop("WHATSAPP_TOKEN", None)
        self.sent = []
        self._orig = whatsapp.send_text
        whatsapp.send_text = lambda to, body: self.sent.append((to, body)) or True
        bot, _ = make()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(bot))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        whatsapp.send_text = self._orig
        self.srv.shutdown()
        self.srv.server_close()
        for k in ("VERIFY_TOKEN", "APP_SECRET"):
            os.environ.pop(k, None)

    def post(self, payload, secret="s3cret"):
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/webhook", body,
                                     {"X-Hub-Signature-256": sig}, method="POST")
        return urllib.request.urlopen(req)

    @staticmethod
    def payload(text="hi", mid="m1"):
        return {"entry": [{"changes": [{"value": {"messages": [
            {"id": mid, "from": "919", "type": "text", "text": {"body": text}}]}}]}]}

    def test_verification_handshake(self):
        url = (f"http://127.0.0.1:{self.port}/webhook?hub.mode=subscribe"
               "&hub.verify_token=tok&hub.challenge=12345")
        self.assertEqual(urllib.request.urlopen(url).read(), b"12345")
        bad = url.replace("tok", "wrong")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(bad)
        self.assertEqual(cm.exception.code, 403)

    def test_message_gets_reply(self):
        self.post(self.payload("hello"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "919")
        self.assertIn("BOOK", self.sent[0][1])

    def test_duplicate_delivery_ignored(self):
        self.post(self.payload("hello", "same"))
        self.post(self.payload("hello", "same"))
        self.assertEqual(len(self.sent), 1)

    def test_bad_signature_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post(self.payload(), secret="wrong")
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.sent, [])

    def test_non_text_message(self):
        p = {"entry": [{"changes": [{"value": {"messages": [
            {"id": "img1", "from": "919", "type": "image"}]}}]}]}
        self.post(p)
        self.assertIn("text", self.sent[0][1])


if __name__ == "__main__":
    unittest.main()
