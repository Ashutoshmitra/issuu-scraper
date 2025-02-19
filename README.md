# Issuu Scraper

Automatically downloads new publications from specified Issuu handles and uploads them to Google Drive.

## Setup

1. Configure Google Drive:
   - Create OAuth 2.0 credentials in Google Cloud Console
   - Enable Google Drive API
   - Add credentials to GitHub secrets

2. Configure Gmail:
   - Create an App Password for Gmail
   - Add it to GitHub secrets

3. Update config.json:
   - Add Issuu handles to track
   - Set notification email addresses
   - Add Google Drive folder ID

## Configuration

Edit `config.json` to modify:
- Issuu handles to track
- Email notification settings
- Google Drive folder location

## How it works

- Runs every 7 days via GitHub Actions
- Checks for new publications after Jan 31, 2025
- Downloads new publications and uploads to Google Drive
- Sends email notifications
- Maintains a log of processed publications

## Manual Trigger

You can manually trigger the scraper:
1. Go to Actions tab
2. Select "Periodic Book Scraper"
3. Click "Run workflow"