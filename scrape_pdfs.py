"""
scrape_pdfs.py
--------------
Search Crossref for journal articles (2024-2025) matching keywords, check Open Access PDF URLs via Unpaywall,
and download up to N PDFs (default 20). Focus keywords include: India, Indian dataset, rare disease, dark skin,
CNN, convolutional neural network, deep learning, literature review, kro.

USAGE:
1. Set your EMAIL variable (required by Unpaywall and recommended for polite Crossref usage).
2. Install dependencies:
    pip install requests tqdm

3. Run:
    python scrape_pdfs.py --keywords "India rare disease dark skin CNN literature review kro"
"""
import argparse
import time
import os
import re
import json
from urllib.parse import quote_plus
import requests
from tqdm import tqdm
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API_TEMPLATE = "https://api.unpaywall.org/v2/{doi}?email={email}"

# Google Gemini API Key from environment variable
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Publishers to focus on
PREFERRED_PUBLISHERS = ["Springer", "IEEE", "Scopus", "Elsevier"]

HEADERS = {
    "User-Agent": "pdf-scraper/1.0 (mailto:your-email@example.com)"
}

def query_crossref(query, start_date="2024-01-01", end_date="2025-12-31", rows=20, offset=0, filter_publishers=True):
    """
    Query Crossref works endpoint for journal articles matching the query in the date range.
    Returns JSON response or None.
    """
    # Build filter string
    filter_parts = [
        f"from-pub-date:{start_date}",
        f"until-pub-date:{end_date}",
        "type:journal-article"
    ]
    
    # Add publisher filter if requested
    if filter_publishers:
        publisher_filter = " OR ".join([f"publisher-name:{pub}" for pub in PREFERRED_PUBLISHERS])
        filter_parts.append(f"({publisher_filter})")
    
    params = {
        "filter": ",".join(filter_parts),
        "rows": rows,
        "offset": offset,
        "query": query
    }
    
    try:
        r = requests.get(CROSSREF_API, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Crossref query failed:", e)
        return None

def get_pdf_url_from_unpaywall(doi, email):
    """
    Use Unpaywall to find OA locations for a DOI. Returns the best PDF URL if available, otherwise None.
    """
    safe_doi = quote_plus(doi)
    url = UNPAYWALL_API_TEMPLATE.format(doi=safe_doi, email=email)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        # look for best oa_location with url_for_pdf or url
        if data.get("best_oa_location"):
            loc = data["best_oa_location"]
            # prefer url_for_pdf, then url
            pdf_url = loc.get("url_for_pdf") or loc.get("url")
            if pdf_url and pdf_url.lower().endswith(".pdf"):
                return pdf_url
            # sometimes the url is a landing page; still return it for manual check
            if pdf_url:
                return pdf_url
        # fallback: check other oa_locations
        for loc in data.get("oa_locations", []):
            pdf_url = loc.get("url_for_pdf") or loc.get("url")
            if pdf_url:
                return pdf_url
    except Exception as e:
        print("Unpaywall lookup failed for DOI", doi, ":", e)
    return None

def sanitize_filename(s):
    s = re.sub(r'[\\/*?:"<>|]', "_", s)
    return s[:200]

def download_file(url, dest_path, sleep_between_chunks=0.1):
    """
    Stream-download a file to dest_path. Returns True on success.
    """
    try:
        with requests.get(url, stream=True, headers=HEADERS, timeout=60) as r:
            r.raise_for_status()
            total = r.headers.get('content-length')
            total = int(total) if total and total.isdigit() else None
            with open(dest_path, "wb") as f, tqdm(total=total, unit='B', unit_scale=True, desc=os.path.basename(dest_path)) as pbar:
                downloaded_size = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        pbar.update(len(chunk))
                        if sleep_between_chunks:
                            time.sleep(sleep_between_chunks)
                
                # Check if the file is empty or too small (less than 1KB)
                if downloaded_size < 1024:
                    print(f"Downloaded file is too small ({downloaded_size} bytes), rejecting...")
                    raise Exception("File too small")
        return True
    except Exception as e:
        print("Download failed:", e)
        if os.path.exists(dest_path):
            try: os.remove(dest_path)
            except: pass
        return False

def is_valid_pdf(file_path):
    """
    Check if a file is a valid PDF by checking its header.
    Returns True if valid, False otherwise.
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(4)
            return header == b'%PDF'
    except:
        return False

def process_item(item, email, outdir, args):
    """
    Process a single item from Crossref results.
    Returns a tuple (success: bool, message: str).
    """
    try:
        doi = item.get("DOI")
        title = item.get("title", [""])[0] if item.get("title") else ""
        journal = item.get("container-title", [""])[0] if item.get("container-title") else ""
        pub_date_parts = item.get("published-print") or item.get("published-online") or item.get("created")
        pub_date = "unknown"
        if pub_date_parts and pub_date_parts.get("date-parts"):
            dp = pub_date_parts.get("date-parts")[0]
            pub_date = "-".join(str(x) for x in dp)
        
        # Check if the item matches our publisher criteria
        publisher = item.get("publisher", "")
        if args.filter_publishers and publisher not in PREFERRED_PUBLISHERS:
            # If we're filtering by preferred publishers and this item doesn't match, skip it
            return False, f"Skipping {doi} - Publisher {publisher} not in preferred list"
        
        if not doi:
            return False, "No DOI found"
        
        print(f"Checking DOI: {doi} | {title} | {journal} | {pub_date} | Publisher: {publisher}")
        pdf_url = get_pdf_url_from_unpaywall(doi, email)
        
        if not pdf_url:
            return False, f"No OA PDF reported by Unpaywall for DOI: {doi}"
        
        # prepare filename
        fn = sanitize_filename(f"{pub_date} - {journal} - {title}.pdf")
        dest_path = os.path.join(outdir, fn)
        # if pdf_url looks like a landing page (html), we still try; user may need to open manually
        print("  Found URL:", pdf_url)
        success = download_file(pdf_url, dest_path)
        
        # Additional validation for the downloaded file
        if success and os.path.exists(dest_path):
            # Check if it's a valid PDF
            if not is_valid_pdf(dest_path):
                print(f"  Downloaded file is not a valid PDF, removing...")
                try: os.remove(dest_path)
                except: pass
                return False, f"Downloaded file is not a valid PDF from: {pdf_url}"
            
            # Check file size again after download
            file_size = os.path.getsize(dest_path)
            if file_size < 1024:
                print(f"  Downloaded file is too small ({file_size} bytes), removing...")
                try: os.remove(dest_path)
                except: pass
                return False, f"Downloaded file is too small from: {pdf_url}"
            
            return True, f"Saved: {dest_path}"
        elif success:
            return False, f"Download completed but file not found: {pdf_url}"
        else:
            return False, f"Failed to download PDF from: {pdf_url}"
    except Exception as e:
        return False, f"Error processing item: {str(e)}"

def generate_search_keywords_from_description(description):
    """Generate search keywords from a research topic description using Google Gemini API"""
    try:
        # Prepare the prompt for Gemini
        prompt = f"""
        Based on the following research topic description, generate a list of 5-10 relevant academic keywords 
        that would be effective for searching research papers. Return only the keywords separated by commas.
        
        Description: {description}
        
        Keywords:
        """
        
        # Call the Gemini API with the new gemini-2.0-flash model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        data = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }]
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        keywords = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return keywords
    except Exception as e:
        print(f"Error generating search keywords: {e}")
        # Fallback to using the description as keywords
        return description

def main(args):
    email = args.email
    if not email or "@" not in email:
        print("Please set a valid email address with --email (Unpaywall requires this).")
        return
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    query = args.keywords
    per_page = 20
    offset = 0
    downloaded = 0
    seen_dois = set()
    max_workers = min(args.max, 5)  # Limit concurrent workers to prevent overwhelming servers
    max_attempts = args.max * 3  # Allow more attempts to ensure we get the requested count
    attempts = 0

    print("Searching Crossref for query:", query)
    if args.filter_publishers:
        print("Filtering by preferred publishers:", ", ".join(PREFERRED_PUBLISHERS))
    else:
        print("Including all publishers")
        
    # Use ThreadPoolExecutor for concurrent processing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        
        # Continue until we have downloaded the requested number or reached max attempts
        while downloaded < args.max and attempts < max_attempts and offset < max_attempts * 5:
            resp = query_crossref(query, start_date=args.start_date, end_date=args.end_date, 
                                 rows=per_page, offset=offset, filter_publishers=args.filter_publishers)
            if not resp:
                break
            items = resp.get("message", {}).get("items", [])
            if not items:
                break
                
            # Submit items for concurrent processing
            for item in items:
                # Stop if we've already submitted enough items
                if len(futures) >= (args.max - downloaded + 10):  # Buffer to account for failures
                    break
                    
                doi = item.get("DOI")
                if not doi or doi in seen_dois:
                    continue
                seen_dois.add(doi)
                attempts += 1
                
                # Submit the item for processing
                future = executor.submit(process_item, item, email, outdir, args)
                futures.append((future, doi))
                
                # Add a small delay to prevent overwhelming the servers
                time.sleep(0.1)
                
            offset += per_page
            
            # Process completed futures
            for future, doi in list(futures):
                try:
                    # Wait for completion with a timeout
                    success, message = future.result(timeout=120)  # 2-minute timeout
                    print(f"  --> {message}")
                    if success:
                        downloaded += 1
                        print(f"  --> Downloaded {downloaded}/{args.max}")
                        
                    # Remove processed future
                    futures.remove((future, doi))
                except concurrent.futures.TimeoutError:
                    print(f"  --> Timeout processing DOI: {doi}")
                    futures.remove((future, doi))
                except Exception as e:
                    print(f"  --> Error processing DOI {doi}: {str(e)}")
                    futures.remove((future, doi))
                
                # Check if we've reached the maximum
                if downloaded >= args.max:
                    break
                    
            # Add a pause between batches
            if downloaded < args.max and futures:
                print(f"Pausing for {args.pause} seconds between batches...")
                time.sleep(args.pause)
        
        # Process any remaining futures
        for future, doi in futures:
            try:
                success, message = future.result(timeout=120)
                print(f"  --> {message}")
                if success:
                    downloaded += 1
                    print(f"  --> Downloaded {downloaded}/{args.max}")
            except concurrent.futures.TimeoutError:
                print(f"  --> Timeout processing DOI: {doi}")
            except Exception as e:
                print(f"  --> Error processing DOI {doi}: {str(e)}")

    print("Finished. Downloaded", downloaded, "PDF(s) to", outdir)
    if downloaded < args.max:
        print(f"Warning: Only able to download {downloaded} out of {args.max} requested PDFs.")
        print("This may be due to limited availability of Open Access PDFs for your search terms.")
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download OA PDFs for literature review papers (2024-2025) using Crossref + Unpaywall")
    parser.add_argument("--keywords", "-k", type=str, required=True, help="Search keywords (e.g. 'India rare disease dark skin CNN literature review')")
    parser.add_argument("--email", "-e", type=str, required=True, help="Your email (required by Unpaywall)")
    parser.add_argument("--max", "-m", type=int, default=20, help="Max number of PDFs to download (default 20)")
    parser.add_argument("--outdir", "-o", type=str, default="downloaded_pdfs", help="Output directory")
    parser.add_argument("--start_date", type=str, default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end_date", type=str, default="2025-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--pause", type=float, default=2.0, help="Seconds to pause between downloads/requests (politeness)")
    parser.add_argument("--filter-publishers", action="store_true", help="Filter by specific publishers (Springer, IEEE, Scopus, Elsevier)")
    args = parser.parse_args()
    main(args)