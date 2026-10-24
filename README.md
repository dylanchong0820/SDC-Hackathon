# Quote Bot AI

Staff-only WhatsApp Business inbox and quotation assistant: receive and archive
messages and attachments, compare selected quotations with AI, record the team's
choice, and send text replies from the business number.

## Quote comparison

In the staff inbox, choose **Compare quotations**, select up to eight incoming
messages or saved attachments, and set the priority to price, delivery time, or
both equally. Only those selected sources are sent to RunPod. Results are saved
for staff to review, but the app never accepts a quotation or sends a reply
automatically. Clearing source message history also removes its saved comparison.

Set `RUNPOD_API_KEY` in Replit Secrets. The app discovers the served model from
the configured endpoint's OpenAI-compatible `/v1/models` route; the endpoint
must also support `/v1/chat/completions`. `RUNPOD_ENDPOINT_URL` can override the
project's configured endpoint. Use an endpoint model that accepts the image and
audio inputs you intend to analyze.

Analysis supports incoming text, JPEG/PNG/WebP images, OGG/MP3/WAV/M4A/AAC/FLAC
audio, TXT/Markdown/CSV/RTF, DOC/DOCX, XLSX, and text-based PDFs. Scanned PDFs
need OCR first; legacy XLS spreadsheets and video files remain archived but are
not analyzed. Processing limits are 5 MB per image/audio file, 9 MB combined
image/audio data, and 20 MB per document. PDF and legacy DOC text extraction use
the `pdftotext` and `antiword` system utilities available in this workspace.

See [replit.md](replit.md) for the complete configuration, storage behavior,
security requirements, tests, and deployment limitations.

---

## Team Information

| Field                  | Details                                     |
| ---------------------- | ------------------------------------------- |
| **Team Name**          | Your Team Name                              |
| **Team Domain**        | Domain 1 / Domain 2 *(choose one)*          |
| **SDG Track (1–17)**   | e.g. SDG 11 — Sustainable Cities and Communities |
| **Team Leader**        | Leader Name                                 |
| **University**         | University Name(s)                          |
| **Number of Members**  | e.g. 4                                      |

## Links

| Resource          | Link                                           |
| ----------------- | ---------------------------------------------- |
| GitHub Repository | https://github.com/your-username/your-project  |
| Replit Project    | https://replit.com/@your-username/your-project |
| Live Demo         | https://your-project.replit.app                |
| RunPod Endpoint   | Configured in `RUNPOD_ENDPOINT_URL`            |
| Slide Deck        | https://example.com/slides                     |
| Demo Video        | https://example.com/demo-video                 |

---

## Overview

### Problem

Describe the real-world problem you are solving in 2–4 sentences. Who is affected, and why does it matter?

### Solution

Explain how your project uses AI to solve the problem and what makes it valuable.

### Key Features

- **Shared WhatsApp inbox** — Receive messages and attachments; staff can reply using the business number.
- **Price comparison** — Compare selected quotations using price, delivery time, or both. A running comparison can be stopped so you can change the selected sources or priority. Results arriving after cancellation are discarded; the current OpenAI-compatible RunPod endpoint may continue processing remotely until it finishes or times out.
- **Quotation register** — Review saved analysis details and export the records as an Excel workbook.
- **Quote AI Chat** — Ask questions using all saved completed comparisons and download each answer as a PDF with company, item, price, and requirement columns. Chat responses are not sent to customers.

### Tech Stack

| Layer         | Technology                   |
| ------------- | ---------------------------- |
| Frontend      | HTML, CSS, vanilla JavaScript |
| Backend       | Python, Flask                 |
| Database      | SQLite                        |
| AI / Compute  | RunPod (GPU)                 |
| Hosting / Dev | Replit                       |

---

## Project Structure

```
.
├── docs/                    # Screenshots, diagrams
├── src/
│   ├── main.py              # Application entry point
│   ├── server.py            # Flask API, website, and webhook
│   ├── storage.py           # WhatsApp archive and quotation records
│   ├── quote_analysis.py    # Local extraction and RunPod comparison
│   └── quotation_export.py  # Excel workbook generation
├── runpod/
│   └── handler.py           # Optional template; not used by the configured vLLM endpoint
├── submission/              # Required submission documents
│   ├── REPLIT PROOF OF USE.pdf
│   ├── RUNPOD PROOF OF USE.pdf
│   └── Submission Template SDC Hackathon.pdf
├── tests/
│   └── test_utils.py        # Unit tests
├── .env.example             # Example environment variables
├── .gitignore
├── .replit                  # Replit run configuration
├── LICENSE
├── requirements.txt         # Python dependencies
└── README.md
```

### Architecture

```
User ──► Replit (App / API) ──► RunPod Endpoint (GPU model)
                │
                └──► Response returned to user
```

---

## How to Run

Quote AI PDF downloads require Chromium to be available on the runtime `PATH`.
Generated PDFs are returned to the browser and are not stored in the inbox archive.

### Option 1 — Run on Replit (Recommended)

1. Open the [Replit Project](https://replit.com/@your-username/your-project), or import this repository via **Create Repl → Import from GitHub**.
2. Add `SESSION_SECRET`, `STAFF_PASSWORD` (12+ characters), `META_ACCESS_TOKEN`,
   `WEBHOOK_VERIFY_TOKEN`, `META_APP_SECRET`, and `RUNPOD_API_KEY` in Replit Secrets.
3. Click **Run** to start `python src/main.py`, then sign in to the website.
4. In Settings, enter the business **phone number ID** from Meta's WhatsApp API
   Setup page. Configure the site's HTTPS URL plus `/webhook` in Meta and
   subscribe to `messages` events.
5. Ask a customer to message the business number, then reply from the website.
   Free-form texts are available only during Meta's 24-hour reply window.

### Option 2 — Run Locally

```bash
# 1. Clone the repository
git clone https://github.com/your-username/your-project.git
cd your-project

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure the variables above in your local environment (or a private .env).

# 5. Run the app
python src/main.py
```

### Run Tests

```bash
python -m unittest discover tests
```

---

## How We Use Replit *(Required)*

Describe how your team used Replit. For example:

- **Development:** Collaborative coding in a shared Repl using Multiplayer.
- **Hosting:** The app is deployed with Replit Deployments at the Live Demo link above.
- **Secrets:** API keys are stored in Replit Secrets, never in the code.

📄 Proof of use: [`submission/REPLIT PROOF OF USE.pdf`](submission/REPLIT%20PROOF%20OF%20USE.pdf)

## How We Use RunPod *(Required)*

The Replit app calls the configured RunPod endpoint's OpenAI-compatible
`/v1/models` and `/v1/chat/completions` routes using `RUNPOD_API_KEY`. Set
`RUNPOD_ENDPOINT_URL` only if the project should use a different endpoint.
`runpod/handler.py` is an unused template for a separately configured custom
handler; it is not part of the current vLLM-based quotation flow.

📄 Proof of use: [`submission/RUNPOD PROOF OF USE.pdf`](submission/RUNPOD%20PROOF%20OF%20USE.pdf)

---

## Challenges & Learnings

- What challenges did you face, and how did you overcome them?
- What did your team learn?

## Future Improvements

- [ ] Improvement 1
- [ ] Improvement 2

## Team

| Name        | Role                | GitHub                                   |
| ----------- | ------------------- | ---------------------------------------- |
| Member Name | Team Lead / Backend | [@username](https://github.com/username) |
| Member Name | Frontend            | [@username](https://github.com/username) |
| Member Name | AI / RunPod         | [@username](https://github.com/username) |

---

## Submission Checklist

- [ ] Team name, domain (Domain 1 / Domain 2) and SDG track filled in
- [ ] Slide deck link added
- [ ] Demo video link added
- [ ] Replit used, with proof of use in `submission/`
- [ ] RunPod used, with proof of use in `submission/`
- [ ] Submission template completed in `submission/`
- [ ] "How to Run" steps verified
- [ ] No API keys or secrets committed to the repository

## License

This project is licensed under the [Apache License 2.0](LICENSE).
