from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from pymongo import MongoClient
from bs4 import BeautifulSoup
import time
import os
import random
import math
import requests
import sys
from itertools import cycle
import zipfile
from queue import Queue, Empty
from threading import Lock, Thread
from contextlib import contextmanager
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


class ProgressTracker:
    """Thread-safe progress tracker"""
    def __init__(self, progress_file="scraper_progress.json"):
        self.progress_file = progress_file
        self.lock = Lock()
        self.current_state = self.load_progress()
    
    def load_progress(self):
        """Load progress from file"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, 'r') as f:
                    state = json.load(f)
                print(f"Loaded progress: Currently at query '{state['current_query']}', person {state['current_person_index']}")
                return state
            except Exception as e:
                print(f"Error loading progress: {e}")
        
        return {
            "current_letter_i": 0,
            "current_letter_j": 0, 
            "current_query": "aa",
            "current_person_index": 0,
            "total_people_processed": 0,
            "completed_queries": []  # Track completed queries to avoid duplicates
        }
    
    def save_progress(self, letter_i, letter_j, query, person_index, total_processed, completed_queries=None):
        """Thread-safe save progress"""
        with self.lock:
            self.current_state = {
                "current_letter_i": letter_i,
                "current_letter_j": letter_j,
                "current_query": query,
                "current_person_index": person_index,
                "total_people_processed": total_processed,
                "completed_queries": completed_queries or self.current_state.get("completed_queries", [])
            }
            
            try:
                with open(self.progress_file, 'w') as f:
                    json.dump(self.current_state, f, indent=2)
            except Exception as e:
                print(f"Error saving progress: {e}")
    
    def mark_query_completed(self, query):
        """Mark a query as completed"""
        with self.lock:
            if "completed_queries" not in self.current_state:
                self.current_state["completed_queries"] = []
            if query not in self.current_state["completed_queries"]:
                self.current_state["completed_queries"].append(query)
                self.save_progress(
                    self.current_state["current_letter_i"],
                    self.current_state["current_letter_j"], 
                    self.current_state["current_query"],
                    self.current_state["current_person_index"],
                    self.current_state["total_people_processed"],
                    self.current_state["completed_queries"]
                )
    
    def is_query_completed(self, query):
        """Check if query was already completed"""
        with self.lock:
            return query in self.current_state.get("completed_queries", [])
    
    def get_current_state(self):
        """Thread-safe get current state"""
        with self.lock:
            return self.current_state.copy()
    
    def clear_progress(self):
        """Clear all progress and start fresh"""
        with self.lock:
            self.current_state = {
                "current_letter_i": 0,
                "current_letter_j": 0, 
                "current_query": "aa",
                "current_person_index": 0,
                "total_people_processed": 0,
                "completed_queries": []
            }
            try:
                if os.path.exists(self.progress_file):
                    os.remove(self.progress_file)
                print("Progress cleared successfully")
            except Exception as e:
                print(f"Error clearing progress: {e}")


class ProxyBrowserPool:
    def __init__(self, proxy_list, pool_size=5):
        self.proxy_list = proxy_list
        self.pool_size = min(pool_size, len(proxy_list))
        self.browser_pool = Queue(maxsize=self.pool_size)
        self.lock = Lock()
        self.extension_counter = 0
        self.initialize_pool()

    def create_proxy_auth_extension(self, proxy):
        """Create Chrome extension for proxy authentication"""
        with self.lock:
            self.extension_counter += 1
            extension_path = os.path.abspath(f"proxy_auth_extension_{self.extension_counter}.zip")
       
        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """

        background_js = f"""
        var config = {{
            mode: "fixed_servers",
            rules: {{
                singleProxy: {{
                    scheme: "http",
                    host: "{proxy['host']}",
                    port: parseInt({proxy['port']})
                }},
                bypassList: ["localhost"]
            }}
        }};
        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

        function callbackFn(details) {{
            return {{
                authCredentials: {{
                    username: "{proxy.get('username', '')}",
                    password: "{proxy.get('password', '')}"
                }}
            }};
        }}

        chrome.webRequest.onAuthRequired.addListener(
            callbackFn,
            {{urls: ["<all_urls>"]}},
            ['blocking']
        );
        """

        with zipfile.ZipFile(extension_path, 'w') as zp:
            zp.writestr("manifest.json", manifest_json)
            zp.writestr("background.js", background_js)
       
        return extension_path

    def create_browser_with_proxy(self, proxy):
        """Create a browser instance with the given proxy"""
        extension_path = self.create_proxy_auth_extension(proxy)
       
        options = Options()
        options.add_extension(extension_path)
        options.add_argument("--start-maximized")
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--ignore-ssl-errors')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--remote-debugging-port=0')
        options.add_argument('--disable-extensions-file-access-check')
        options.add_argument('--disable-extensions-http-throttling')
        options.add_experimental_option('useAutomationExtension', False)
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('detach', True)  # Keep browser open

        try:
            # Use WebDriver Manager to automatically download and manage ChromeDriver
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
           
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # Test the browser by navigating to a simple page
            try:
                driver.get('https://www.google.com')
                time.sleep(2)
                print(f"Browser with proxy {proxy['host']}:{proxy['port']} is working")
            except Exception as e:
                print(f"Browser test failed: {e}")
                driver.quit()
                return None
           
            time.sleep(3)
            return driver
           
        except Exception as e:
            print(f"Error creating browser with proxy {proxy['host']}:{proxy['port']}: {e}")
            return None

    def initialize_pool(self):
        """Initialize the browser pool with different proxies"""
        for proxy in self.proxy_list[:self.pool_size]:
            browser = self.create_browser_with_proxy(proxy)
            if browser:
                self.browser_pool.put((browser, proxy))
                print(f"Added browser with proxy {proxy['host']}:{proxy['port']} to pool")

    @contextmanager
    def get_browser(self, timeout=30):
        """Thread-safe context manager to get a browser from the pool"""
        try:
            browser, proxy = self.browser_pool.get(timeout=timeout)
            thread_id = threading.current_thread().ident
            print(f"Thread {thread_id}: Using proxy {proxy['host']}:{proxy['port']}")
            
            # Reset browser state
            browser.delete_all_cookies()
            browser.get('about:blank')
            yield browser
            
        except Empty:
            print(f"Thread {threading.current_thread().ident}: No browser available, skipping")
            yield None
        finally:
            if 'browser' in locals() and browser:
                self.browser_pool.put((browser, proxy))

    def cleanup(self):
        """Clean up all browsers in the pool"""
        while not self.browser_pool.empty():
            browser, _ = self.browser_pool.get()
            try:
                browser.quit()
            except:
                pass
       
        for i in range(1, self.extension_counter + 1):
            ext_file = f"proxy_auth_extension_{i}.zip"
            if os.path.exists(ext_file):
                try:
                    os.remove(ext_file)
                except:
                    pass


class MultithreadedGEDSScraper:
    def __init__(self, browser_pool, mongo_client, max_workers=3):
        self.browser_pool = browser_pool
        self.mongo_client = mongo_client
        self.db = mongo_client["geds_directory"]
        self.collection = self.db["people"]
        self.max_workers = max_workers
        self.progress_tracker = ProgressTracker()
        self.person_counter = 0
        self.session_start_count = self.collection.count_documents({})
        self.counter_lock = Lock()
        
        # Load saved progress
        saved_state = self.progress_tracker.get_current_state()
        self.person_counter = saved_state['total_people_processed']

    def extract_person_info(self, soup, driver):
        """Extract person information from page"""
        name_tag = soup.select_one("h3.panel-title")
        full_name = name_tag.get_text(strip=True) if name_tag else ""
        name_parts = full_name.split()
        first_name = name_parts[1] if len(name_parts) > 1 else ""
        last_name = name_parts[0] if len(name_parts) > 0 else ""

        email_tag = soup.select_one("a[href^='mailto:']")
        email = email_tag.get_text(strip=True) if email_tag else ""

        tel_tag = soup.find("div", class_="dttn")
        work_phone = tel_tag.get_text(strip=True) if tel_tag else ""

        org_tags = soup.select("section.panel-default div.browseLink a")
        hierarchy = [tag.get_text(strip=True) for tag in org_tags]
        department = hierarchy[1] if len(hierarchy) > 1 else ""
        organization = " > ".join(hierarchy[2:]) if len(hierarchy) > 2 else ""

        return {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "email": email,
            "work_phone": work_phone,
            "department": department,
            "organization": organization,
            "organization_hierarchy": hierarchy,
            "source_url": driver.current_url,
            "scraped_at": time.time()
        }

    def scrape_query(self, query):
        """Scrape a single query (e.g., 'aa', 'ab', etc.) - runs in separate thread"""
        thread_id = threading.current_thread().ident
        print(f"\nThread {thread_id}: Starting query {query}")
        
        # Check if already completed
        if self.progress_tracker.is_query_completed(query):
            print(f"Thread {thread_id}: Query {query} already completed, skipping")
            return
        
        try:
            with self.browser_pool.get_browser() as driver:
                if not driver:
                    print(f"Thread {thread_id}: No browser available for query {query}")
                    return
                
                # Navigate to search
                driver.get("https://www.geds-sage.gc.ca/en/GEDS?pgid=002")
                time.sleep(random.uniform(2, 5))

                search_box = driver.find_element(By.ID, "sv")
                search_box.clear()
                search_box.send_keys(query)
                search_box.send_keys(Keys.RETURN)
                time.sleep(random.uniform(3, 5))

                page_count = 0
                # Pagination loop
                while True:
                    page_count += 1
                    person_links = driver.find_elements(By.XPATH, "//div/ol/li/a[1][contains(@href, 'pgid=')]")
                    print(f"Thread {thread_id}: Query {query}, Page {page_count} - Found {len(person_links)} people")

                    # Process each person on this page
                    for k, person in enumerate(person_links):
                        name = person.text.strip()
                        if not name:
                            continue

                        print(f"Thread {thread_id}: Query {query} - Visiting: {name}")
                        
                        try:
                            driver.execute_script("arguments[0].scrollIntoView(true);", person)
                            time.sleep(random.uniform(0.3, 1))
                            driver.execute_script("arguments[0].click();", person)

                            WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, "h3.panel-title"))
                            )

                            soup = BeautifulSoup(driver.page_source, "html.parser")
                            document = self.extract_person_info(soup, driver)

                            if not any([document["full_name"], document["email"], document["work_phone"]]):
                                print(f"Thread {thread_id}: No useful data for {name}, skipping...")
                                driver.back()
                                time.sleep(random.uniform(2, 4))
                                continue

                            # Check for duplicates (thread-safe)
                            existing = self.collection.find_one({
                                "first_name": document["first_name"],
                                "last_name": document["last_name"],
                                "email": document["email"]
                            })

                            if existing:
                                print(f"Thread {thread_id}: Duplicate found for {document['full_name']}, skipping...")
                            else:
                                self.collection.insert_one(document)
                                print(f"Thread {thread_id}: Saved: {document['full_name']}")

                            # Increment counter (thread-safe)
                            with self.counter_lock:
                                self.person_counter += 1
                                if self.person_counter % 10 == 0:
                                    print(f"Thread {thread_id}: Total people processed: {self.person_counter}")

                        except Exception as e:
                            print(f"Thread {thread_id}: Error processing {name}: {e}")

                        driver.back()
                        time.sleep(random.uniform(2, 4))

                    # Try to go to next page
                    try:
                        next_button = driver.find_element(By.XPATH, "(//ul[contains(@class, 'pagination')]/li/a[@href='#'])[last()]")
                        driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                        time.sleep(random.uniform(0.5, 1.5))
                        next_button.click()
                        time.sleep(random.uniform(3, 5))
                    except Exception:
                        print(f"Thread {thread_id}: No more pages for query {query}")
                        break

                # Mark query as completed
                self.progress_tracker.mark_query_completed(query)
                print(f"Thread {thread_id}: Completed query {query}")

        except Exception as e:
            print(f"Thread {thread_id}: Error scraping query {query}: {e}")

    def generate_query_list(self):
        """Generate list of queries to process"""
        letters = [chr(i) for i in range(ord('a'), ord('z') + 1)]
        queries = []
        
        for i in range(len(letters)):
            for j in range(len(letters)):
                query = letters[i] + letters[j]
                if not self.progress_tracker.is_query_completed(query):
                    queries.append(query)
        
        return queries

    def scrape_multithreaded(self):
        """Main scraping function using multiple threads"""
        queries = self.generate_query_list()
        print(f"Starting multithreaded scraping with {self.max_workers} threads")
        print(f"Processing {len(queries)} queries")

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all queries to thread pool
                future_to_query = {executor.submit(self.scrape_query, query): query 
                                 for query in queries}
                
                # Process completed futures
                for future in as_completed(future_to_query):
                    query = future_to_query[future]
                    try:
                        future.result()
                        print(f"Completed processing query: {query}")
                    except Exception as e:
                        print(f"Query {query} generated an exception: {e}")

        except KeyboardInterrupt:
            print("\nScraping interrupted by user.")
            print("Progress has been saved and can be resumed later.")
        except Exception as e:
            print(f"Unexpected error: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        final_count = self.collection.count_documents({})
        print(f"\nFinal documents in collection: {final_count}")
        print(f"Documents added this session: {final_count - self.session_start_count}")
        print(f"Total people processed: {self.person_counter}")
       
        self.browser_pool.cleanup()
        self.mongo_client.close()
        print("Browser pool and MongoDB connection closed.")


class ProxyRotator:
    def __init__(self, api_url, auth_token):
        self.api_url = api_url
        self.auth_token = auth_token
        self.proxy_list = []
        self.load_proxies()
   
    def load_proxies(self):
        """Load proxies from Webshare API"""
        MAX_PAGE_SZ = 100
       
        def fetch_page(page, page_size=MAX_PAGE_SZ, mode="direct"):
            params = {"mode": mode, "page": page, "page_size": page_size}
            headers = {"Authorization": f"Token {self.auth_token}"}
            try:
                resp = requests.get(self.api_url, headers=headers, params=params, timeout=(5,15))
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                print(f"[ERROR] fetching page {page}: {e}", file=sys.stderr)
                return None

        first = fetch_page(page=1)
        if not first or "count" not in first:
            print("Failed to fetch proxies.", file=sys.stderr)
            return

        total_count = first["count"]
        pages_needed = math.ceil(total_count / MAX_PAGE_SZ)
        print(f"Total proxies: {total_count}, fetching {pages_needed} pages…")

        all_results = first.get("results", [])
        for p in range(2, pages_needed + 1):
            data = fetch_page(page=p)
            if data and "results" in data:
                all_results.extend(data["results"])

        self.proxy_list = []
        for proxy_data in all_results:
            ip = proxy_data.get("proxy_address") or proxy_data.get("ip")
            port = proxy_data.get("proxy_port") or proxy_data.get("port")
            if ip and port:
                self.proxy_list.append({
                    "host": ip,
                    "port": port,
                    "username": proxy_data.get("username", ""),
                    "password": proxy_data.get("password", "")
                })

        print(f"Loaded {len(self.proxy_list)} proxies")


def main():
    # Configuration
    API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"          
    AUTH_TOKEN = ""    # ← PUT YOUR WEBSHARE TOKEN HERE
    MAX_WORKERS = 3    # Reduced for stability - Number of concurrent threads
    BROWSER_POOL_SIZE = 5  # Reduced for stability - Should be >= MAX_WORKERS

    # MongoDB setup  
    try:
        client = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        current_count = client["geds_directory"]["people"].count_documents({})
        print(f"Connected to MongoDB. Current document count: {current_count}")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return

    # Initialize proxy rotator
    proxy_rotator = ProxyRotator(API_URL, AUTH_TOKEN)
    if not proxy_rotator.proxy_list:
        print("No proxies loaded. Exiting.")
        return

    # Initialize browser pool
    browser_pool = ProxyBrowserPool(proxy_rotator.proxy_list, BROWSER_POOL_SIZE)

    # Initialize and run scraper
    scraper = MultithreadedGEDSScraper(browser_pool, client, MAX_WORKERS)
   
    # Ask if user wants to clear previous progress
    if os.path.exists("scraper_progress.json"):
        response = input("Previous progress found. Do you want to resume? (y/n): ").lower()
        if response == 'n':
            scraper.progress_tracker.clear_progress()
            print("Progress cleared. Starting fresh.")
   
    scraper.scrape_multithreaded()


if __name__ == "__main__":
    main()