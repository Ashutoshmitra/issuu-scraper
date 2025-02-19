import requests
from bs4 import BeautifulSoup
import json
import os
from PIL import Image
import io
from tqdm import tqdm
import img2pdf
import time
import argparse
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import unicodedata
import re


logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class IssuuScraper:
    def __init__(self, max_workers=10):
        self.max_workers = max_workers
        self.session = self._create_session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://issuu.com/',
        }

    def _create_session(self):
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        # Mount the adapter with retry strategy for both HTTP and HTTPS
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_maxsize=self.max_workers)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session

    def get_document_data(self, url):
        """Extract document data including ID and page count."""
        try:
            logger.info(f"Fetching document data from URL: {url}")
            response = self.session.get(url, headers=self.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            initial_data = soup.find('script', {'id': 'initial-data'})
            
            if initial_data and initial_data.get('data-json'):
                data = json.loads(initial_data['data-json'])
                doc_data = data.get('initialDocumentData', {}).get('document', {})
                
                if doc_data:
                    logger.info(f"Found document data for: {doc_data.get('title', 'Unknown')}")
                    logger.info(f"Publication date: {doc_data.get('originalPublishDateInISOString', 'Not found')}")
                    return {
                        'publication_id': doc_data.get('publicationId'),
                        'page_count': doc_data.get('pageCount', 0),
                        'title': doc_data.get('title'),
                        'revision_id': doc_data.get('revisionId'),
                        'originalPublishDateInISOString': doc_data.get('originalPublishDateInISOString')
                    }
                else:
                    logger.error("No document data found in parsed JSON")
            
            logger.error("Could not find initial-data script tag or data-json attribute")
            raise ValueError("Could not find document data")
            
        except Exception as e:
            logger.error(f"Error getting document data: {str(e)}")
            logger.error(f"URL was: {url}")
            return None

    def download_page_image(self, doc_id, revision_id, page_num, output_path):
        """Download a single page image."""
        try:
            image_url = f"https://image.isu.pub/{revision_id}-{doc_id}/jpg/page_{page_num}.jpg"
            
            # Add randomized delay between 0.1 and 0.3 seconds
            time.sleep(random.uniform(0.1, 0.3))
            
            image_headers = self.headers.copy()
            image_headers.update({
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            })
            
            response = self.session.get(image_url, headers=image_headers)
            response.raise_for_status()
            
            img = Image.open(io.BytesIO(response.content))
            img.save(output_path, 'JPEG', quality=95)
            return True
            
        except Exception as e:
            logger.error(f"Error downloading page {page_num}: {str(e)}")
            return False

    def download_page_batch(self, args):
        """Helper function for parallel downloads."""
        doc_id, revision_id, page_num, output_path = args
        return page_num, self.download_page_image(doc_id, revision_id, page_num, output_path)

    def create_pdf(self, image_folder, output_pdf):
        """Create PDF from downloaded images."""
        try:
            image_files = sorted([os.path.join(image_folder, f) for f in os.listdir(image_folder) 
                                if f.endswith('.jpg')])
            
            if not image_files:
                logger.error("No images found to create PDF")
                return False
                
            logger.info(f"Creating PDF from {len(image_files)} images")
            with open(output_pdf, 'wb') as pdf_file:
                pdf_file.write(img2pdf.convert(image_files))
            return True
            
        except Exception as e:
            logger.error(f"Error creating PDF: {str(e)}")
            return False

    def sanitize_filename(self, filename):
        """
        Sanitize the filename by removing/replacing invalid characters.
        
        Args:
            filename (str): The original filename
            
        Returns:
            str: Sanitized filename safe for all operating systems
        """
        # Step 1: Convert unicode characters to ASCII equivalents where possible
        filename = unicodedata.normalize('NFKD', filename)
        filename = ''.join(c for c in filename if not unicodedata.combining(c))
        
        # Step 2: Replace specific characters with safe alternatives
        char_map = {
            '<': '(',
            '>': ')',
            ':': '-',
            '"': "'",
            '/': '-',
            '\\': '-',
            '|': '-',
            '?': '',
            '*': '',
            '&': 'and',
            '#': 'No.',
            '%': 'pct',
            '{': '(',
            '}': ')',
            '~': '-',
            '+': 'plus',
            '@': 'at',
            '!': '',
            '`': "'",
            '=': '-',
            ';': ',',
            '[': '(',
            ']': ')',
        }
        
        for char, replacement in char_map.items():
            filename = filename.replace(char, replacement)
        
        # Step 3: Remove any other non-printable characters and control characters
        filename = ''.join(char for char in filename if char.isprintable())
        
        # Step 4: Replace multiple spaces/dashes with single ones
        filename = re.sub(r'\s+', ' ', filename)  # Multiple spaces to single space
        filename = re.sub(r'-+', '-', filename)   # Multiple dashes to single dash
        
        # Step 5: Strip spaces and dashes from beginning and end
        filename = filename.strip(' -')
        
        # Step 6: Ensure filename isn't too long (max 255 chars is safe for most filesystems)
        if len(filename) > 255:
            name_part, ext_part = os.path.splitext(filename)
            filename = name_part[:255 - len(ext_part)] + ext_part
            
        # Step 7: Ensure we still have a valid filename
        if not filename:
            filename = "unnamed_document"
            
        return filename

    def scrape_publication(self, handle, pub_url, progress_callback=None):
        """Scrape a single publication."""
        try:
            doc_data = self.get_document_data(pub_url)
            if not doc_data or not doc_data['publication_id']:
                logger.error("Could not get publication data")
                return False

            publication_id = doc_data['publication_id']
            revision_id = doc_data['revision_id']
            page_count = doc_data['page_count']
            original_title = doc_data['title']
            sanitized_title = self.sanitize_filename(original_title)

            logger.info(f"Processing {original_title} (ID: {publication_id}) with {page_count} pages")
            logger.info(f"Sanitized title: {sanitized_title}")

            # Create output directories
            base_dir = f"downloads/{handle}/{publication_id}"
            images_dir = f"{base_dir}/images"
            os.makedirs(images_dir, exist_ok=True)
            
            # Download pages in parallel
            successful_downloads = 0
            failed_pages = []
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for page_num in range(1, page_count + 1):
                    output_path = f"{images_dir}/page_{page_num:03d}.jpg"
                    futures.append(
                        executor.submit(
                            self.download_page_image,
                            doc_id=publication_id,
                            revision_id=revision_id,
                            page_num=page_num,
                            output_path=output_path
                        )
                    )
                
                for i, future in enumerate(as_completed(futures), 1):
                    success = future.result()
                    if success:
                        successful_downloads += 1
                        if progress_callback:
                            progress_callback(original_title, i, page_count, "downloading")
                    else:
                        failed_pages.append(i)
                        logger.error(f"Failed to download page {i}")

            if successful_downloads == 0:
                logger.error("No pages were downloaded successfully")
                return False

            # Retry failed pages
            if failed_pages:
                logger.info(f"Retrying {len(failed_pages)} failed pages")
                for page_num in failed_pages:
                    output_path = f"{images_dir}/page_{page_num:03d}.jpg"
                    success = self.download_page_image(
                        publication_id, 
                        revision_id,
                        page_num,
                        output_path
                    )
                    if success:
                        successful_downloads += 1

            # Create PDF
            if successful_downloads > 0:
                pdf_path = f"{base_dir}/{sanitized_title}.pdf"
                pdf_created = self.create_pdf(images_dir, pdf_path)
                
                if pdf_created:
                    logger.info(f"Successfully created PDF: {pdf_path}")
                    if progress_callback:
                        progress_callback(original_title, page_count, page_count, "completed")
                    return True

            return False
            
        except Exception as e:
            logger.error(f"Error in scrape_publication: {str(e)}")
            return False

    def get_publications(self, handle, num_publications):
        """Get publication URLs with pagination support."""
        base_url = f"https://issuu.com/{handle}"
        pub_urls = []
        page = 1
        
        while len(pub_urls) < num_publications:
            try:
                if page == 1:
                    url = base_url
                else:
                    url = f"{base_url}?page={page}"
                    
                logger.info(f"Fetching page {page} from {url}")
                response = self.session.get(url, headers=self.headers)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                pub_cards = soup.find_all('div', {'data-testid': 'publication-card'})
                
                if not pub_cards:
                    logger.info(f"No more publications found on page {page}")
                    break
                    
                logger.info(f"Found {len(pub_cards)} publication cards on page {page}")
                
                for card in pub_cards:
                    link = card.find('a', href=lambda x: x and f'/{handle}/docs/' in x)
                    if link and not link['href'].endswith('/docs/') and 'http' not in link['href']:
                        full_url = f"https://issuu.com{link['href']}"
                        if full_url not in pub_urls:  # Avoid duplicates
                            pub_urls.append(full_url)
                            if len(pub_urls) >= num_publications:
                                break
                
                page += 1
                # Add a small delay between page requests
                time.sleep(random.uniform(1, 2))
                
            except Exception as e:
                logger.error(f"Error getting publications on page {page}: {str(e)}")
                break
        
        logger.info(f"Total publications found: {len(pub_urls)}")
        return pub_urls[:num_publications]  # Ensure we don't return more than requested

def main():
    parser = argparse.ArgumentParser(description='Download books from Issuu')
    parser.add_argument('handle', help='Issuu user handle')
    parser.add_argument('n', type=int, help='Number of books to download')
    parser.add_argument('--workers', type=int, default=10, help='Number of concurrent downloads')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    scraper = IssuuScraper(max_workers=args.workers)
    base_url = f"https://issuu.com/{args.handle}"
    
    try:
        response = requests.get(base_url, headers=scraper.headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        pub_links = soup.find_all('a', href=lambda x: x and f'/{args.handle}/docs/' in x)
        
        pub_urls = list(set([f"https://issuu.com{link['href']}" for link in pub_links 
                           if not link['href'].endswith('/docs/') and 'http' not in link['href']]))
        
        if not pub_urls:
            logger.error("No publications found. Check the handle.")
            return

        logger.info(f"Found {len(pub_urls)} publications")
        pub_urls = pub_urls[:args.n]
        
        for pub_url in pub_urls:
            logger.info(f"Processing publication: {pub_url}")
            if scraper.scrape_publication(args.handle, pub_url):
                logger.info(f"Successfully downloaded: {pub_url}")
            else:
                logger.error(f"Failed to download: {pub_url}")
            
            time.sleep(random.uniform(1, 2))

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        if args.debug:
            import traceback
            logger.debug(traceback.format_exc())

if __name__ == "__main__":
    main()