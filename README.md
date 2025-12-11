# AI QQ Email Support Agent

An AI-powered email handling system built with LangGraph framework, specifically designed to automate customer support for QQ emails. The system automatically receives, categorizes, searches relevant documentation, and replies to customer emails.

## Key Features

- Automatic monitoring of new QQ emails
- AI-powered intelligent email classification (by type and urgency)
- Document search based on email content
- Automatic generation of professional and accurate responses
- Human review for complex emails
- Automatic bug reporting to Feishu
- Automated email replies

## Technical Architecture

This project is built on the following technology stack:

- [LangGraph](https://langchain-ai.github.io/langgraph/) - Building language agent workflows
- [LangChain](https://www.langchain.com/) - Building AI applications
- Compatible chat model APIs with API keys - Various AI model services
- Python 3.11.x

## Requirements

- Python 3.11.x
- QQ email account and authorization code
- API key for compatible chat model (OpenAI, DeepSeek, etc.)
- Feishu app credentials (APP_ID, APP_SECRET, APP_TOKEN)

## Installation

### 1. Clone the project

```bash
git clone <repository-url>
cd AIHandleQQEmail
```

### 2. Install dependencies

Install directly from pyproject.toml:

```bash
pip install -e .
```

Or build and install the package:

```bash
python -m build
pip install dist/*.whl
```

### 3. Set environment variables

Before running the program, set the following environment variables:

```bash
export QQEMAIL=your_qq_email@qq.com        # QQ email address
export EMAIL_PASSWORD=your_email_password  # QQ email authorization code
export MODEL=your_model_name               # Model name to use
export BASE_URL=your_api_base_url          # API base URL
export API_KEY=your_api_key                # API key
export APP_ID=your_feishu_app_id           # Feishu app ID
export APP_SECRET=your_feishu_app_secret   # Feishu app secret
export APP_TOKEN=your_feishu_app_token     # Feishu app token
export RECEIVE_ID=your_receive_id          # Feishu receive ID for messages
export TABLE_ID=your_table_id              # Feishu table ID for bug tracking
```

Notes:
- QQ email requires an authorization code instead of login password, which can be generated in QQ email settings
- MODEL, BASE_URL, API_KEY can be configured according to the model service used (e.g. OpenAI, DeepSeek, etc.)
- Feishu credentials are needed for bug tracking and messaging features

## Usage

### Run in test mode

By default, runs in test mode:

```bash
python ThinkingInLangGraph.py
```

### Run in production mode

Pass False parameter to run in production mode:

```bash
python ThinkingInLangGraph.py False
```

### As a command-line tool

After installation, you can use the following command:

```bash
ThinkingInLangGraph
```

### Run as executable

Build the executable using the build script:

```bash
./build.sh
```

The executable will be located in the `dist/` directory.

## Workflow

1. **Email Monitoring**: Continuously monitors new emails in QQ mailbox
2. **Email Classification**: Uses AI to analyze email content and classify by intent and urgency
3. **Document Search**: Searches relevant documentation based on classification results
4. **Response Drafting**: Drafts responses based on search results and email content
5. **Special Handling**:
   - Complex or high-priority emails are sent for human review via Feishu
   - Bug reports are automatically recorded in Feishu database
6. **Email Sending**: Automatically sends reply emails to customers

## Email Classification Rules

The system classifies emails based on the following dimensions:

- **Intent(intent)**:
  - question: General inquiries
  - bug: Bug reports
  - building: Deployment-related issues
  - feature: Feature requests
  - complex_request: Complex requests

- **Urgency(urgency)**:
  - low: Low priority
  - medium: Medium priority
  - high: High priority
  - critical: Critical

## Configuration

### GitHub Actions Auto-build and Test

The project includes GitHub Actions configuration file for automatic building and testing.

The following secrets need to be configured in repository settings:
- MODEL: Model name to use
- BASE_URL: API base URL
- API_KEY: API key
- APP_ID: Feishu app ID
- APP_SECRET: Feishu app secret
- APP_TOKEN: Feishu app token
- RECEIVE_ID: Feishu receive ID for messages
- TABLE_ID: Feishu table ID for bug tracking
- QQEMAIL: QQ email address
- EMAIL_PASSWORD: QQ email authorization code

## Project Structure

```
.
├── QQEmailListener.py       # QQ email listener implementation
├── ThinkingInLangGraph.py   # Core AI processing logic
├── build.sh                 # Build script for executable
├── pyproject.toml           # Project configuration file
└── .github/workflows/
    └── build-and-test.yml   # GitHub Actions build and test configuration
```

## Development Guide

### Main Components

1. **QQEmailListener class**: Responsible for listening to QQ emails and fetching new emails
2. **ThinkingInLangGraph main workflow**: LangGraph-based workflow that handles email classification, document search, response generation, etc.
3. **Feishu Integration**: Integration with Feishu for bug tracking and messaging
4. **Various processing nodes**:
   - classify_intent: Email classification node
   - search_documentation: Document search node
   - bug_tracking: Bug tracking node
   - to_human: Human escalation node
   - draft_response: Response drafting node
   - send_reply: Send reply node

### Extending Functionality

You can extend the following features based on your requirements:
- Add more email classification types
- Integrate more powerful document search systems
- Add multi-language support
- Add email template management
- Enhance human review interface

## Notes

1. IMAP service must be enabled for QQ email
2. Authorization code must be used instead of login password for QQ email
3. Ensure stable network connection to access AI API
4. It is recommended to add error handling and logging in production environments

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.