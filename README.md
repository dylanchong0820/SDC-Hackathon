# WhatsApp Desk

---

## Team Information

| Field                  | Details                                     |
| ---------------------- | ------------------------------------------- |
| **Team Name**          | Ctrl + Sleep                                |
| **Team Domain**        |  Domain 2                                   |
| **SDG Track (1–17)**   | SDG 9                                       |
| **Team Leader**        | Chin Xian Jing                              |
| **University**         | University of Nottingham Malaysia           |
| **Number of Members**  | 4                                           |

## Links

| Resource          | Link                                           |
| ----------------- | ---------------------------------------------- |
| GitHub Repository | https://github.com/your-username/your-project  |
| Replit Project    | https://replit.com/@your-username/your-project |
| Live Demo         | https://your-project.replit.app                |
| RunPod Endpoint   | https://api.runpod.ai/v2/your-endpoint-id      |
| Slide Deck        | https://example.com/slides                     |
| Demo Video        | https://example.com/demo-video                 |

---

## Overview

### Problem
The manual processing of invoices and the extraction of financial data is tedious, prone to errors, and time-consuming, causing bottlenecks for the accounting staff, SMEs, and the finance department. The redundant process decreases efficiency and slows down the process of generating the financial report. With the help of an automated DeepSeek AI parser that is cost-efficient, it is possible to prevent human error and minimize the processing time.


### Solution

The project focuses on solving manual document processing issues by employing a quantized DeepSeek-V4.1-Flash model, which is running on the serverless infrastructure of RunPod and can automatically process, extract, and structure data from complicated invoices with a high level of precision. The system employs the model through a standard vLLM API, compatible with the OpenAI API, and thus immediately replaces manual work with instant document processing in a seamless manner. The key advantage of this technology is in its outstanding combination of performance, cost-effectiveness, and integratability. The use of 2.9bpw EXL3 quantization allows one to run the latest LLM on affordable GPUs, and the serverless Load Balancer architecture automatically shrinks to zero when the model is not in use, meaning businesses pay only for processing time.

### Key Features

- **Feature 1** — Whatsapp AI helper will read all the document in all of the chat of busniess account.
- **Feature 2** — 
- **Feature 3** — Short description.

### Tech Stack

| Layer         | Technology                   |
| ------------- | ---------------------------- |
| Frontend      | e.g. HTML, React             |
| Backend       | e.g. Python, Flask / FastAPI |
| Database      | e.g. PostgreSQL, SQLite      |
| AI / Compute  | RunPod (GPU)                 |
| Hosting / Dev | Replit                       |

---

## Project Structure

```
.
├── docs/                    # Screenshots, diagrams
├── runpod/
│   └── handler.py           # RunPod serverless handler (GPU workload)
├── src/
│   ├── main.py              # Application entry point
│   └── utils.py             # Helper functions
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

### Option 1 — Run on Replit (Recommended)

1. Open the [Replit Project](https://replit.com/@your-username/your-project), or import this repository via **Create Repl → Import from GitHub**.
2. Add `SESSION_SECRET`, `STAFF_PASSWORD` (12+ characters), `META_ACCESS_TOKEN`,
   `WEBHOOK_VERIFY_TOKEN`, and `META_APP_SECRET` in Replit Secrets.
3. Click **Run** to start `python src/server.py`, then sign in to the website.
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
python src/server.py
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

Describe how your team used RunPod. For example:

- **Workload:** Model inference (e.g. text generation, image processing) runs on a RunPod GPU.
- **Setup:** `runpod/handler.py` is deployed as a **RunPod Serverless Endpoint**.
- **Integration:** The Replit app sends requests to the endpoint using `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID`.

Example request:

```bash
curl -X POST https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/runsync \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"prompt": "Hello"}}'
```

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
