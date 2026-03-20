# MVP Scope

## Product

- UI language: Traditional Chinese
- U.S. stocks: English names
- Taiwan stocks: Traditional Chinese names
- No login
- One local watchlist
- Real-time-ready architecture with mock streaming during MVP

## MVP Features

- Search U.S. and Taiwan stocks
- Add/remove items from a single watchlist
- Quote cards with latest price, change, and market status
- Stock detail page with simple price chart
- Backend provider abstraction for future real-time vendors

## Notes

- Taiwan stock discovery now uses official TWSE/TPEx market-master data, and Taiwan quotes plus recent TWSE daily history come from official TWSE endpoints.
- U.S. stock discovery now uses the SEC company ticker master file.
- U.S. quotes and any remaining unsupported history ranges still use mock providers to keep the API contract stable.
- Real-time vendor integration should replace the remaining mock provider paths without changing the frontend contract.
