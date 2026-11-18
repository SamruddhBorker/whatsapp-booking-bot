# whatsapp-booking-bot

[![tests](https://github.com/SamruddhBorker/whatsapp-booking-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/SamruddhBorker/whatsapp-booking-bot/actions/workflows/tests.yml)

A WhatsApp table-booking bot for small restaurants, cafés and salons.
Customers message the business number and book, check or cancel a reservation
without anyone picking up the phone, including after hours.

**The problem:** small businesses lose bookings because nobody answers messages
at night, and staff waste hours repeating the same answers (hours, location,
"is there a table at 7?").

- Python 3.9+, **standard library only**, SQLite storage
- Try it right now in your terminal, no WhatsApp account needed
- Plugs into the WhatsApp Cloud API when you're ready to go live

## What customers can do

```
Customer: hi
Bot:      Hi! Welcome to Coastal Café 👋  Reply with: BOOK, MY BOOKING, CANCEL, HOURS, LOCATION or MENU
Customer: book
Bot:      Great, let's book a table! What date?
Customer: saturday
Bot:      Sat 21 Nov, got it. What time?
Customer: 7pm
Bot:      How many people? (1 to 8)
Customer: 4
Bot:      And the name for the booking?
Customer: Priya
Bot:      Please confirm: 📅 Sat 21 Nov 🕖 7:00 PM 👥 4 people 👤 Priya. Reply YES to confirm.
Customer: yes
Bot:      ✅ Booked! Reference B0001
```

Also: opening hours, location, menu link, custom FAQs (Wi-Fi, parking, vegan
options...), view your booking, cancel it. Understands "today", "tomorrow",
"Saturday", "25 Nov", "7pm", "19:30".

## Try it (no account needed)

```bash
python simulate.py
```

## Make it yours

Edit [`config.json`](config.json): business name, opening hours, slot length,
tables per slot, max party size, closed days, location and menu links, and
your own FAQ entries.

## Go live on WhatsApp

1. Create a Meta developer app and add the **WhatsApp** product
   ([Cloud API getting started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started)).
2. Copy `.env.example` to `.env` and fill in the token, phone number ID,
   a verify token you choose, and the app secret.
3. Run the webhook: `python server.py`
4. Expose it over HTTPS (Cloudflare Tunnel or ngrok) and set
   `https://<your-url>/webhook` as the callback URL in Meta, with your verify token.
5. Message your test number.

Optional: set `OWNER_PHONE` to get a WhatsApp alert for every new booking or
cancellation. Schedule `python reminders.py` once a day to send day-before reminders.

## Safety and design

- Webhook requests are **signature-verified** (`X-Hub-Signature-256`) when `APP_SECRET` is set
- Secrets live in `.env`, which is git-ignored
- Duplicate webhook deliveries are ignored, so no double replies or double bookings
- Booking is **atomic** (SQLite transaction): two people can't take the last table
- Per-customer limit on active bookings, party-size cap, input length cap and name sanitising
- Customers can only cancel their own bookings
- The server binds to `127.0.0.1` by default

## Known limits (honest list)

- WhatsApp lets a business start a conversation only with an **approved message
  template** once the customer's 24-hour window closes. Day-before reminders
  need such a template for production use (see the note in `reminders.py`).
- Capacity is "tables per time slot", not real table layouts.
- One business per deployment; no staff dashboard yet.

## Layout

| File | Role |
|---|---|
| `engine.py` | conversation logic and date/time parsing |
| `storage.py` | SQLite bookings, sessions, de-duplication |
| `whatsapp.py` | Cloud API sending, signature check, payload parsing |
| `server.py` | webhook server |
| `simulate.py` | terminal chat for testing |
| `reminders.py` | daily reminder job |

## Tests

```bash
python -m unittest -v
```

## License

MIT
