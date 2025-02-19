import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from datetime import timezone
from dateutil.parser import parse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from issuu_scraper import IssuuScraper
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive.file']
CUTOFF_DATE = datetime(2025, 1, 31, tzinfo=timezone.utc)
PROCESSED_PUBS_FILE = os.path.join('data', 'processed_publications.json')
CONFIG_FILE = 'config.json'

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def load_processed_publications():
    try:
        with open(PROCESSED_PUBS_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse {PROCESSED_PUBS_FILE}, creating new file")
                default_data = {"processed_publications": []}
                with open(PROCESSED_PUBS_FILE, 'w') as f:
                    json.dump(default_data, f, indent=2)
                return default_data
    except FileNotFoundError:
        logger.warning(f"{PROCESSED_PUBS_FILE} not found, creating new file")
        default_data = {"processed_publications": []}
        os.makedirs(os.path.dirname(PROCESSED_PUBS_FILE), exist_ok=True)
        with open(PROCESSED_PUBS_FILE, 'w') as f:
            json.dump(default_data, f, indent=2)
        return default_data

def save_processed_publication(pub_id, metadata):
    data = load_processed_publications()
    data["processed_publications"].append({
        "publication_id": pub_id,
        "metadata": metadata,
        "processed_at": datetime.now().isoformat()
    })
    with open(PROCESSED_PUBS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def is_publication_processed(pub_id):
    data = load_processed_publications()
    return any(pub["publication_id"] == pub_id for pub in data["processed_publications"])

def get_google_drive_service():
    try:
        credentials = service_account.Credentials.from_service_account_file(
            'credentials.json', 
            scopes=SCOPES
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"Error creating Drive service: {str(e)}")
        raise

def upload_to_drive(service, file_path, folder_id):
    try:
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, mimetype='application/pdf')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        logger.info(f"Successfully uploaded file: {file.get('webViewLink')}")
        return file.get('id'), file.get('webViewLink')
    except Exception as e:
        logger.error(f"Error uploading to Drive: {str(e)}")
        raise

def format_email_body(new_books):
    body = "Hello!\n\n"
    body += f"The scraper has found {len(new_books)} new publication(s) since the last check:\n\n"
    
    for book in new_books:
        body += f"📚 {book['title']}\n"
        body += f"   Published: {book['publish_date']}\n"
        body += f"   Handle: {book['handle']}\n"
        body += f"   Pages: {book['page_count']}\n"
        body += f"   Google Drive Link: {book['drive_link']}\n\n"
    
    body += "\nBest regards,\nYour Issuu Scraper Bot 🤖"
    return body

def send_email(subject, body, config):
    try:
        sender_email = config['sender_email']
        password = os.environ['EMAIL_PASSWORD']

        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = ", ".join(config['notification_emails'])
        message["Subject"] = subject

        message.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, password)
            server.send_message(message)
            logger.info("Email notification sent successfully")
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        raise

def main():
    logger.info("Starting scraper job")
    try:
        config = load_config()
        logger.info("Config loaded successfully")
        
        # Initialize services
        drive_service = get_google_drive_service()
        logger.info("Google Drive service initialized")
        
        scraper = IssuuScraper()
        new_books = []
        
        for handle in config['issuu_handles']:
            logger.info(f"Processing handle: {handle}")
            publications = scraper.get_publications(handle, 10)  # Check last 10 publications
            logger.info(f"Found {len(publications)} publications for {handle}")
            
            for pub_url in publications:
                doc_data = scraper.get_document_data(pub_url)
                if not doc_data:
                    logger.warning(f"Could not get document data for {pub_url}")
                    continue
                
                pub_id = doc_data['publication_id']
                
                # Skip if already processed
                if is_publication_processed(pub_id):
                    logger.info(f"Publication {pub_id} already processed, skipping")
                    continue
                
                # Check publication date
                logger.info(f"Document data: {json.dumps(doc_data, indent=2)}")
                pub_date_str = doc_data.get('originalPublishDateInISOString')
                if not pub_date_str:
                    logger.warning(f"No publication date found for {doc_data.get('title', 'Unknown title')}")
                    continue
                logger.info(f"Found publication date: {pub_date_str}")
                pub_date = parse(pub_date_str)
                if pub_date <= CUTOFF_DATE:
                    logger.info(f"Publication {pub_id} is before cutoff date, skipping")
                    continue
                
                # Download the publication
                logger.info(f"Downloading publication: {doc_data['title']}")
                success = scraper.scrape_publication(handle, pub_url)
                
                if success:
                    pdf_path = f"downloads/{handle}/{pub_id}/{doc_data['title']}.pdf"
                    
                    # Upload to Google Drive
                    file_id, web_link = upload_to_drive(
                        drive_service, 
                        pdf_path, 
                        config['google_drive_folder_id']
                    )
                    
                    book_info = {
                        'title': doc_data['title'],
                        'handle': handle,
                        'publish_date': pub_date.strftime('%Y-%m-%d'),
                        'page_count': doc_data['page_count'],
                        'publication_id': pub_id,
                        'drive_link': web_link
                    }
                    
                    new_books.append(book_info)
                    
                    # Save to processed publications
                    save_processed_publication(pub_id, book_info)
                    logger.info(f"Successfully processed: {doc_data['title']}")
        
        # Send email if new books were found
        if new_books:
            subject = f"New Issuu Publications Found - {datetime.now().strftime('%Y-%m-%d')}"
            body = format_email_body(new_books)
            send_email(subject, body, config)
            logger.info(f"Notification sent for {len(new_books)} new publications")
        else:
            logger.info("No new publications found")

    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
        raise

if __name__ == "__main__":
    main()