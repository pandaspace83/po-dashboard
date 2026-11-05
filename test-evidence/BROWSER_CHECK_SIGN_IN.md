# Browser check: sign-in (PD-1.9d)

A person follows this in a real browser. It uses a throwaway data folder, so it never touches real data, and it uses
made-up addresses only. **Do not write real email addresses or passwords in this file.** Record results on paper or in a
private note; the repository is public.

Time: about 15 minutes. Browsers: do it once in Safari and once in Chrome (they treat cookies differently).

## Set up (Terminal, in the project folder)

```sh
export PO_DASHBOARD_DATA="$HOME/Projects/po-dashboard-check-data"
uv run po-dashboard user create-admin boss@example.com     # type a password of your choice, twice; nothing shows as you type
uv run po-dashboard serve                                  # leave this window open
```

Open http://127.0.0.1:8000 in the browser.

## Steps

Tick each one. **Expected** is what you should see. If anything differs, write down what you saw and stop there.

| # | Do this | Expected | Safari | Chrome |
|---|---|---|---|---|
| 1 | Open http://127.0.0.1:8000 | You land on the sign-in page (the address changes to `/sign-in`). No account details are shown. | | |
| 2 | Sign in with a wrong password | "The email or password is not right." You stay on the sign-in page. | | |
| 3 | Sign in with an email that does not exist | The **same** message as step 2, word for word. | | |
| 4 | Sign in as `boss@example.com` with your real password | You reach the home page. Your email shows at the top, with a **People** link. | | |
| 5 | Press the browser's Back button, then reload | You are still signed in and the page is not stale. | | |
| 6 | Click **People**, then add `buyer@example.com` as a buyer | A box shows a 20-character temporary password, "shown once". **Copy it somewhere safe for step 8.** | | |
| 7 | Click the **People** link again (do not use the browser's Reload: it re-sends the form and shows "That email already has an account", which is correct) | The temporary password is **gone** and cannot be shown again. | | |
| 8 | Click **Sign out**, then sign in as `buyer@example.com` with the temporary password | You are taken straight to **Change password**. Clicking the Home link bounces you back to it. | | |
| 9 | Try a short new password (for example `abc`) | It is refused with a clear reason. | | |
| 10 | Choose a good new password (12+ characters) | You reach the home page. There is **no** People link for a buyer. | | |
| 11 | As the buyer, type http://127.0.0.1:8000/admin/users in the address bar | "Admins only" (a refusal, not the People page). | | |
| 12 | Sign out. Sign in as `boss@example.com`, People, **Disable** the buyer, then try to sign in as the buyer | The buyer gets the ordinary "not right" message. | | |
| 13 | Click **Enable** for the buyer, then sign in as the buyer with a **wrong** password 5 times in a row | The fifth or sixth try says "Too many wrong attempts. Try again in 15 minutes." | | |
| 14 | Now use the **right** password for the locked buyer | Still refused as locked. (Then, as boss, click **Unlock**: the buyer can sign in again.) | | |
| 15 | Sign in as boss. Leave the tab alone for 31 minutes, then click anything | You are sent to the sign-in page. *(Optional: skip if short of time; the automated tests cover this.)* | | |
| 16 | Sign in as boss in Chrome, and also in Safari. Sign out in one | The other browser stays signed in until you sign out there too. | | |

## Things to look at while you do it

- The browser's address bar shows `127.0.0.1:8000` throughout, with no warnings.
- Pages work on a **narrow window** (drag it to phone width): no sideways scrolling, buttons reachable.
- Switch macOS to **dark mode** and reload: the app stays light and readable (it has one light theme, DEC-35).
- On each page the cursor is **already blinking in the first box** (sign-in: Email; Change password: Current password; People: the Add a person email).
- **Tab** through the sign-in page: you can reach every field and the button without the mouse.
- In the browser's developer tools (Application / Storage → Cookies): `po_session` is marked **HttpOnly** and
  **SameSite: Strict**. There is no `Secure` flag yet; that comes with HTTPS in Phase 6.

## Clean up

Press Ctrl-C in the window running the app, then:

```sh
rm -r "$HOME/Projects/po-dashboard-check-data"
unset PO_DASHBOARD_DATA
```

## Result

Date: ______  Who: ______  Safari: pass / fail  Chrome: pass / fail  Notes (no real addresses or passwords):
