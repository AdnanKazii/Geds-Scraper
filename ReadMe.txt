# GEDS Scraper

## Purpose
This tool scrapes employee data from the Government Electronic Directory Services (GEDS) website using multithreaded automation with proxy rotation. It systematically collects government employee information and stores it in a MongoDB database.

## Important Links
- Official GEDS Website: https://geds-sage.gc.ca/en/GEDS?pgid=002
- Webshare Proxy Service: https://www.webshare.io/

## What it does:
- Downloads employee information from GEDS automatically using multiple concurrent browsers
- Uses proxy rotation to avoid rate limiting and IP blocks
- Saves scraped data to MongoDB database with duplicate detection
- Tracks scraping progress and allows resuming from interruptions
- Processes all possible two-letter search combinations (aa, ab, ac, etc.)

## Tech Stack:
- Python 3.13+
- Selenium WebDriver with Chrome
- MongoDB for data storage
- WebDriver Manager (automatic ChromeDriver management)
- Webshare.io proxies for IP rotation
- BeautifulSoup for HTML parsing
- Threading for concurrent operations

## Prerequisites:
1. **MongoDB**: Must be running locally on port 27017
2. **Chrome Browser**: Latest version installed
3. **Webshare Account**: Get API token from https://www.webshare.io/
4. **Python Environment**: Virtual environment with required packages

## Setup Instructions:

### 1. Install MongoDB
Download and install MongoDB Community Edition, ensure it's running as a service.

### 2. Get Webshare API Token
1. Create account at https://www.webshare.io/
2. Go to your dashboard and copy your API token
3. Add the token to line 539 in `geds_scrapper.py`:
   ```python
   AUTH_TOKEN = "your_token_here"
   ```

### 3. Install Dependencies
The script uses a virtual environment with the following packages:
- selenium
- webdriver-manager  
- pymongo
- beautifulsoup4
- requests

### 4. Configuration Options
You can adjust these settings in the `main()` function:
- `MAX_WORKERS`: Number of concurrent threads (default: 26)
- `BROWSER_POOL_SIZE`: Number of browser instances (default: 31)

## How to Run:

### Method 1: Using Virtual Environment (Recommended)
```powershell
& ".venv\Scripts\python.exe" geds_scrapper.py
```

### Method 2: Direct Python
```powershell
python geds_scrapper.py
```

## Features:
- **Progress Tracking**: Automatically saves progress to `scraper_progress.json`
- **Resume Capability**: Can resume scraping from where it left off
- **Duplicate Detection**: Prevents duplicate records in database
- **Proxy Rotation**: Uses different IP addresses to avoid blocking
- **Error Handling**: Robust error handling and logging
- **Thread Safety**: Safe concurrent operations on shared resources

## Output:
Data is stored in MongoDB:
- Database: `geds_directory`
- Collection: `people`
- Fields: name, email, phone, department, organization, etc.

## Notes:
- ChromeDriver is automatically managed (no manual updates needed)
- Script can be interrupted with Ctrl+C and resumed later
- Monitor MongoDB for scraped data
- Adjust thread counts based on your system capabilities and proxy limits

