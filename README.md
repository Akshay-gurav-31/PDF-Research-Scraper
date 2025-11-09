# PDF Research Scraper

An intelligent academic paper discovery and extraction platform that uses Google Gemini AI to analyze research topics and automatically scrape relevant PDFs from academic databases.

## Features

- **AI-Powered Topic Analysis**: Uses Google Gemini 2.0 Flash to analyze research descriptions and break them into relevant sub-topics
- **Automated PDF Scraping**: Automatically searches and downloads Open Access PDFs from Crossref and Unpaywall
- **Intelligent Keyword Generation**: Converts natural language descriptions into effective academic search keywords
- **Multi-Topic Processing**: Handles complex research topics by breaking them into manageable sub-topics
- **Real-time Progress Tracking**: Shows live scraping progress and results
- **Organized Download**: Collects all PDFs into a single downloadable zip file

## How It Works

1. **User Input**: Enter a detailed research topic or concept in natural language
2. **AI Analysis**: Gemini 2.0 Flash analyzes the description and breaks it into relevant sub-topics
3. **Keyword Generation**: For each sub-topic, the system generates specific academic keywords
4. **PDF Scraping**: Searches Crossref and Unpaywall databases for Open Access PDFs using the generated keywords
5. **Collection**: Downloads and organizes all found PDFs
6. **Download**: Provides a single zip file containing all collected research papers

## Prerequisites

- Python 3.7 or higher
- Flask
- Requests
- python-dotenv
- tqdm

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd pdf-research-scraper
   ```

2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the project root with your Gemini API key:
   ```
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

## Usage

1. Start the application:
   ```bash
   python app.py
   ```

2. Open your browser and navigate to `http://localhost:5000`

3. Enter your research topic in detail and click "Start Scraping"

4. Monitor the progress in real-time

5. Download the collected PDFs when the process completes

## Project Structure

```
pdf-research-scraper/
├── app.py              # Flask web application
├── scrape_pdfs.py      # PDF scraping logic
├── .env               # API key storage (not in version control)
├── .gitignore         # Git ignore file
├── requirements.txt   # Python dependencies
├── README.md          # This file
└── templates/
    └── index.html     # Web interface
```

## How to Get a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Create an account or sign in
3. Navigate to API Keys section
4. Create a new API key
5. Copy the key and add it to your `.env` file

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a pull request

## License

This project is licensed under the MIT License.