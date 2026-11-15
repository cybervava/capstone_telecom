"""Curated, version-stable word lists: cities, areas, names and streets."""

from __future__ import annotations

CITIES: tuple[str, ...] = ("BLR", "MUM", "DEL", "HYD", "CHN", "PUN", "KOL", "AMD")
CITY_WEIGHTS: tuple[float, ...] = (0.18, 0.18, 0.18, 0.12, 0.10, 0.09, 0.08, 0.07)
PIN_PREFIX: dict[str, str] = {
    "BLR": "560", "MUM": "400", "DEL": "110", "HYD": "500",
    "CHN": "600", "PUN": "411", "KOL": "700", "AMD": "380",
}  # fmt: skip

AREAS: dict[str, tuple[str, ...]] = {
    "BLR": ("Indiranagar", "Koramangala", "Jayanagar", "Whitefield", "HSR Layout",
            "Malleshwaram", "Hebbal", "Electronic City", "Marathahalli", "Banashankari",
            "Yelahanka", "Rajajinagar", "BTM Layout", "JP Nagar", "Basavanagudi", "Bellandur"),
    "MUM": ("Andheri West", "Bandra West", "Powai", "Dadar", "Borivali", "Malad", "Goregaon",
            "Chembur", "Kurla", "Worli", "Colaba", "Juhu", "Mulund", "Ghatkopar", "Vile Parle",
            "Santacruz"),
    "DEL": ("Karol Bagh", "Lajpat Nagar", "Dwarka", "Rohini", "Saket", "Vasant Kunj",
            "Janakpuri", "Pitampura", "Mayur Vihar", "Greater Kailash", "Hauz Khas",
            "Rajouri Garden", "Preet Vihar", "Malviya Nagar", "Shahdara", "Paschim Vihar"),
    "HYD": ("Banjara Hills", "Jubilee Hills", "Gachibowli", "Madhapur", "Kondapur",
            "Kukatpally", "Secunderabad", "Begumpet", "Ameerpet", "Miyapur", "Uppal",
            "LB Nagar", "Mehdipatnam", "Dilsukhnagar", "Manikonda", "Tolichowki"),
    "CHN": ("Adyar", "T Nagar", "Anna Nagar", "Velachery", "Mylapore", "Besant Nagar",
            "Tambaram", "Porur", "Guindy", "Nungambakkam", "Kodambakkam", "Sholinganallur",
            "Chromepet", "Perungudi", "Royapettah", "Egmore"),
    "PUN": ("Kothrud", "Baner", "Aundh", "Hinjewadi", "Viman Nagar", "Kharadi", "Hadapsar",
            "Wakad", "Shivaji Nagar", "Koregaon Park", "Pimple Saudagar", "Magarpatta",
            "Camp", "Deccan", "Katraj", "Bavdhan"),
    "KOL": ("Salt Lake", "Park Street", "Ballygunge", "New Town", "Behala", "Dum Dum",
            "Tollygunge", "Garia", "Jadavpur", "Gariahat", "Alipore", "Rajarhat", "Kasba",
            "Shyambazar", "Bhowanipore", "Lake Town"),
    "AMD": ("Navrangpura", "Satellite", "Bopal", "Maninagar", "Vastrapur", "Prahlad Nagar",
            "Thaltej", "Chandkheda", "Naranpura", "Gota", "Paldi", "Ghatlodia", "Vejalpur",
            "Nikol", "Bodakdev", "Sabarmati"),
}  # fmt: skip

FIRST_NAMES: tuple[str, ...] = (
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan",
    "Rohan", "Kabir", "Ananya", "Diya", "Saanvi", "Aadhya", "Kiara", "Myra", "Anika", "Riya",
    "Priya", "Neha", "Pooja", "Sneha", "Kavya", "Meera", "Lakshmi", "Divya", "Nisha", "Rahul",
    "Amit", "Suresh", "Ramesh", "Vikram", "Sanjay", "Rajesh", "Manoj", "Deepak", "Karthik",
    "Naveen", "Harish", "Ganesh", "Farhan", "Imran", "Ayesha", "Zara", "Sara", "Joseph",
    "Maria", "Anjali", "Shreya", "Tanvi", "Bhavna", "Gaurav", "Nikhil", "Varun", "Pranav",
    "Siddharth", "Aishwarya", "Lavanya", "Gurpreet", "Harpreet", "Tenzin", "Arnav", "Ira",
)  # fmt: skip
LAST_NAMES: tuple[str, ...] = (
    "Sharma", "Verma", "Gupta", "Iyer", "Iyengar", "Nair", "Menon", "Reddy", "Rao", "Naidu",
    "Patel", "Shah", "Mehta", "Desai", "Joshi", "Kulkarni", "Deshpande", "Patil", "Pawar",
    "Singh", "Kaur", "Gill", "Chopra", "Kapoor", "Malhotra", "Khanna", "Bose", "Banerjee",
    "Chatterjee", "Mukherjee", "Das", "Ghosh", "Sen", "Pillai", "Krishnan", "Subramanian",
    "Hegde", "Shetty", "Kamath", "Khan", "Qureshi", "Siddiqui", "Fernandes", "Dsouza",
    "Thomas", "Mathew", "Agarwal", "Bansal", "Jain", "Trivedi", "Pandey", "Mishra", "Yadav",
    "Chauhan", "Rathore", "Bhat", "Saxena", "Tiwari",
)  # fmt: skip

STREET_NAMES: tuple[str, ...] = (
    "Lakeview", "Temple", "Station", "Church", "Market", "Park", "Hill", "Gandhi", "Nehru",
    "MG", "Ring", "Old Airport", "Brigade", "Residency", "College", "Mill", "Canal", "Garden",
    "Sunrise", "Palm Grove", "Rose", "Lotus", "Banyan", "Mango", "Coconut", "Riverside",
    "Ashoka", "Tagore", "Shivaji", "Sardar Patel",
)  # fmt: skip
STREET_KINDS: tuple[str, ...] = ("Road", "Street", "Main Road", "Cross", "Lane", "Marg", "Avenue")
BUILDING_KINDS: tuple[str, ...] = ("Apartments", "Residency", "Towers", "Enclave", "Heights")
