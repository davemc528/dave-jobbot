# Dave Jobbot Chrome extension

The extension is a supervised Manifest V3 client for the loopback-only Dave Jobbot bridge.
Candidate data and business logic remain in the local Python application.

## Build and install

```bash
cd chrome_extension
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Then:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Choose `chrome_extension/dist`.
5. Start the bridge:

   ```bash
   PYTHONPATH=src uv run python -m jobbot.cli extension serve
   ```

6. In another terminal, create a one-time code:

   ```bash
   PYTHONPATH=src uv run python -m jobbot.cli extension pair
   ```

7. Enter the six-digit code in the popup and pin the extension.

For development rebuilds:

```bash
cd chrome_extension
npm run dev
```

The bridge listens only on `127.0.0.1:8765`. Capture, tailoring, approval, page filling,
and continuation each require a user action. Passwords, MFA, CAPTCHAs, and final submission
remain manual.
