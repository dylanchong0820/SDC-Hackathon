# Quote Bot AI

Staff-only WhatsApp Business inbox and quotation assistant: receive and archive
messages and attachments, compare selected quotations with AI, record the team's
choice, and send text replies from the business number.

## Quote comparison

In the staff inbox, choose **Compare quotations**, select up to eight incoming
messages or saved attachments, and set the priority to price, delivery time, or
both equally. Choose **Review selected** to extract text locally without calling
RunPod. Check the text and explicitly tick the sources you approve, then choose
**Send confirmed to AI**. Failed extractions cannot be confirmed. Reviews expire
after ten minutes and are bound to your staff session; changing the source
selection requires another review. Images and voice attachments are sent unchanged
and can be opened separately before confirmation; this step does not transcribe them.
Only confirmed sources are sent to RunPod. Results are saved
for staff to review, but the app never accepts a quotation or sends a reply
automatically. Clearing source message history also removes its saved comparison.
Local preview has a two-minute combined extraction budget. The app verifies that
source content still matches the review before sending it to AI. Original files
are never rewritten by review or comparison.

Comparisons run in the background. The page reports local-reading and RunPod
progress, then displays each quotation's validated result as it finishes. Up to
two per-quotation requests run at once; each has a 10-second connection timeout
and a 240-second response-read timeout. Timeout, busy-endpoint, and format errors
are shown per source, while successful results are retained. Partial prices are
labeled in their original currencies; final MYR conversion, ranking, and selection
are available only after the comparison finishes. Staff can keep using other
pages while it runs.

Set `RUNPOD_API_KEY` in Replit Secrets. The app discovers the served model from
the configured endpoint's OpenAI-compatible `/v1/models` route; the endpoint
must also support `/v1/chat/completions`. `RUNPOD_ENDPOINT_URL` can override the
project's configured endpoint. Use an endpoint model that accepts the image and
audio inputs you intend to analyze.

Analysis supports incoming text, JPEG/PNG/WebP images, OGG/MP3/WAV/M4A/AAC/FLAC
audio, TXT/Markdown/CSV/RTF, DOC/DOCX, XLS/XLSX, and PDFs. Scanned PDFs are OCRed
locally in English, Malay, and Simplified/Traditional Chinese, using at most the
first five pages and a 90-second OCR limit. Legacy XLS files are converted in a
temporary directory before reading; the original WhatsApp attachment is unchanged.
For partly searchable PDFs, pages with little or no native text are OCRed when
they fall within that same five-page limit. Pages that cannot be OCRed are called
out in source review and saved comparisons so staff can check the original file.
Processing limits are 5 MB per image/audio file, 9 MB combined image/audio data,
and 20 MB per document. Scans longer than five pages show their total page count
and omitted page range beside the reviewed source and in the saved comparison.
If the page count cannot be verified, staff see an explicit coverage warning.
PDF, OCR, legacy Excel, and legacy Word reading use the
`pdfinfo`, `pdftotext`, `pdftoppm`, `tesseract`, `ssconvert`, and `antiword` utilities
configured in `.replit`. Video files remain archived but are not analyzed.

See [replit.md](replit.md) for the complete configuration, storage behavior,
security requirements, tests, and deployment limitations.

---

## Team Information

| Field                  | Details                                     |
| ---------------------- | ------------------------------------------- |
| **Team Name**          | Ctrl + Sleep                                |
| **Team Domain**        |  Domain 2                                   |
| **SDG Track (1–17)**   | e.g. SDG 8.3Target 8.3 focuses on promoting development-oriented policies that support productive activities, entrepreneurship, innovation, and the growth and formalization of micro-, small-, and medium-sized enterprises (MSMEs) through financial access |
| **Team Leader**        | Chin Xian Jing                              |
| **University**         | University of Nottingham Malaysia           |
| **Number of Members**  | 4                                           |

## Links

| Resource          | Link                                           |
| ----------------- | ---------------------------------------------- |
| GitHub Repository |https://github.com/dylanchong0820/SDC-Hackathon.git  |
| Replit Project    | https://replit.com/@jovilim67/SDC-Hackathon
| Live Demo         | https://youtu.be/23ucObwWn5o              |
| RunPod Endpoint   | https://by40gb92whagr1.api.runpod.ai            |
| Slide Deck        |  https://xmueducn-my.sharepoint.com/:p:/g/personal/fis2508047_xmu_edu_my/IQCMzHE0LrcjSpsaZ5UH_qwCAYv5TYET5Zx-jwpB08kmEp8?e=S9uFwx  |
| Demo Video        | https://youtu.be/23ucObwWn5o                |

---

## Overview

### Problem

In emerging markets, over 80% of B2B micro-, small-, and medium-sized enterprises (MSMEs) rely on unstructured WhatsApp communications, forcing procurement officers and business owners to spend up to 15 hours weekly manually rekeying pricing, quantity, and payment terms into spreadsheets. This process suffers from a high administrative error rate and slow response times, leading to miscommunication and lost revenue. Furthermore, without standardized digital records, small merchants remain trapped in informality, making it difficult to access formal supply chain financing or build credit histories to scale their operations.

### Solution

QuoteBot AI leverages multi-modal Vision-LLMs integrated directly with the Meta WhatsApp Cloud API to parse unstructured incoming data—including text messages, voice notes, photos of handwritten quotes, and multi-page PDFs—into standardized JSON structures. It automatically extracts critical fields like line items, unit prices, supplier details, and payment terms to generate instant chat summaries and downloadable, professional PDF reports within seconds.

The core value lies in its zero-friction, in-chat workflow: non-technical micro- and small-enterprise (MSME) owners do not need to install new software or learn complex ERP systems. By eliminating manual data entry, QuoteBot AI cuts quote processing times by up to 85%, reduces costly pricing errors, and transforms chaotic chat logs into structured, audit-ready digital transaction histories that help small merchants formalize their businesses and access supply chain financing.

### Key Features

- **Shared WhatsApp inbox** — Receive messages and attachments; staff can reply using the business number.
- **Price comparison** — Compare selected quotations using price, delivery time, or both. A running comparison can be stopped so you can change the selected sources or priority. Results arriving after cancellation are discarded; the current OpenAI-compatible RunPod endpoint may continue processing remotely until it finishes or times out.
- **Quotation register** — Review saved analysis details and export the records as an Excel workbook.
- **MYR price comparisons** — Price and balanced rankings use Malaysian Ringgit. Foreign quotes show their original amount, MYR equivalent, rate, date, and source in comparison views and the register; Excel includes separate original/MYR columns. Frankfurter's public daily reference rates are cached for an hour (no API key and no quote contents shared). Missing, unsupported, or more-than-seven-day-old rates are flagged and block overall price recommendations rather than comparing unlike currencies. Saved conversion snapshots stay fixed for auditability; existing comparisons are upgraded when opened, with original files and team selections preserved. Reference amounts exclude bank fees and may differ from payment rates. Mixed-currency chat PDFs also include a factual four-column MYR totals reference.
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
Supplier quotations arrive in highly variable formats, including handwritten receipts, multi-page PDFs, low-resolution photos, and colloquial voice/text messages with implicit business terms.

Solution: We implemented multi-modal Vision-LLMs backed by strict JSON Schema constraints (Structured Outputs). This guarantees that extracted data—such as unit prices, quantities, and payment terms—always map into a predictable, schema-validated JSON format regardless of input quality.



- What did your team learn?
Design for User Context, Not Just Technical Capability
Mastering Structured LLM Outputs for Production
Serverless GPU Orchestration & Cost Control


## Future Improvements

- [ ] Autonomous Multi-Quote Negotiation & Vendor Follow-ups
- [ ] Direct ERP & Accounting Software Sync

## Team

| Name           |     Role                              | GitHub                            |
| -----------    | -------------------            | ---------------------------------------- |
| Chin Xian Jing       | Team Lead / Slide / demo | [@username](https://github.com/username) |
| Dylan Chong Jia Ying | Frontend                 | [@username](https://github.com/username) |
| Zackary Sim Kah Yao  | Slide                    | [@username](https://github.com/username) |
| Jovi Lim Kai Piin    | AI / RunPod              | [@username](https://github.com/username) |
---

## Submission Checklist

- [ √] Team name, domain (Domain 1 / Domain 2) and SDG track filled in
- [ √] Slide deck link added
- [ √] Demo video link added
- [ √] Replit used, with proof of use in `submission/`
- [ √] RunPod used, with proof of use in `submission/`
- [ √] Submission template completed in `submission/`
- [ √] "How to Run" steps verified
- [ √] No API keys or secrets committed to the repository

## License

This project is licensed under the [Apache License 2.0](LICENSE).
