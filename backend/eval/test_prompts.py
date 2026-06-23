"""
SpecForge — Eval Test Prompts
20 prompts: 10 normal + 10 edge cases (4 vague, 4 conflicting, 4 incomplete)
"""

NORMAL_PROMPTS = [
    {
        "id": "n01",
        "category": "normal",
        "label": "CRM",
        "prompt": (
            "Build a CRM for a B2B sales team. "
            "Sales reps can manage contacts, companies, and deals through a pipeline. "
            "Managers can see team analytics and assign leads. "
            "There's a free tier (up to 100 contacts) and a Pro tier ($29/mo) with unlimited contacts and analytics."
        ),
    },
    {
        "id": "n02",
        "category": "normal",
        "label": "Marketplace",
        "prompt": (
            "Build a two-sided marketplace for freelance designers. "
            "Clients post projects, designers submit proposals, and the platform takes a 10% fee on completed contracts. "
            "Admins moderate flagged content. Payment via Stripe Connect."
        ),
    },
    {
        "id": "n03",
        "category": "normal",
        "label": "Booking Platform",
        "prompt": (
            "Build an appointment booking platform for salons and spas. "
            "Business owners can set up services, staff, and availability. "
            "Customers can browse, book, pay, and cancel appointments. "
            "Automated SMS reminders via Twilio."
        ),
    },
    {
        "id": "n04",
        "category": "normal",
        "label": "Blog/CMS",
        "prompt": (
            "Build a multi-author blog platform. "
            "Authors write and publish posts with categories and tags. "
            "Editors review and approve before publishing. "
            "Readers can comment (with moderation). "
            "Premium subscribers see ad-free content and exclusive posts."
        ),
    },
    {
        "id": "n05",
        "category": "normal",
        "label": "Inventory",
        "prompt": (
            "Build an inventory management system for a warehouse. "
            "Staff can add/remove stock items, log stock movements, and set reorder alerts. "
            "Managers see dashboards with low-stock alerts and movement reports. "
            "Supports barcode scanning via camera."
        ),
    },
    {
        "id": "n06",
        "category": "normal",
        "label": "LMS",
        "prompt": (
            "Build a learning management system for corporate training. "
            "HR Admins create courses with video lessons and quizzes. "
            "Employees enroll, complete modules, and earn certificates. "
            "Managers track team completion rates."
        ),
    },
    {
        "id": "n07",
        "category": "normal",
        "label": "E-commerce",
        "prompt": (
            "Build an e-commerce store for handmade crafts. "
            "Sellers list products with photos, descriptions, and pricing. "
            "Buyers browse, add to cart, checkout with Stripe, and track orders. "
            "Admin manages categories and handles refunds."
        ),
    },
    {
        "id": "n08",
        "category": "normal",
        "label": "Project Management",
        "prompt": (
            "Build a project management tool similar to Trello. "
            "Teams create boards with lists and cards. "
            "Cards have assignees, due dates, checklists, and file attachments. "
            "Free plan: 3 boards. Business plan ($12/user/mo): unlimited boards + time tracking."
        ),
    },
    {
        "id": "n09",
        "category": "normal",
        "label": "HR Tool",
        "prompt": (
            "Build an HR management platform. "
            "HR Admins manage employee records, departments, and positions. "
            "Employees submit leave requests and view their own payslips. "
            "Managers approve/reject leave and view their team's attendance."
        ),
    },
    {
        "id": "n10",
        "category": "normal",
        "label": "Analytics Dashboard",
        "prompt": (
            "Build a SaaS analytics dashboard for e-commerce stores. "
            "Users connect their Shopify store via OAuth and see revenue, orders, and customer metrics. "
            "Basic tier: 30-day history. Pro tier ($49/mo): 2-year history, cohort analysis, and CSV export."
        ),
    },
]

EDGE_PROMPTS = [
    # ── Vague ────────────────────────────────────────────────────────
    {
        "id": "e01",
        "category": "edge_vague",
        "label": "Vague - business app",
        "prompt": "Build me an app for my business.",
    },
    {
        "id": "e02",
        "category": "edge_vague",
        "label": "Vague - social network",
        "prompt": "I want a social network like Instagram but better.",
    },
    {
        "id": "e03",
        "category": "edge_vague",
        "label": "Vague - marketplace",
        "prompt": "Something like Airbnb but for cars or maybe boats I'm not sure yet.",
    },
    {
        "id": "e04",
        "category": "edge_vague",
        "label": "Vague - tool",
        "prompt": "Make a tool that helps people be more productive.",
    },

    # ── Conflicting ──────────────────────────────────────────────────
    {
        "id": "e05",
        "category": "edge_conflicting",
        "label": "Conflicting - permissions",
        "prompt": (
            "Build a document management system. Everyone can edit everything. "
            "But only Admins can edit documents. Regular users can only view. "
            "Also all users should be able to create and delete any document they want."
        ),
    },
    {
        "id": "e06",
        "category": "edge_conflicting",
        "label": "Conflicting - monetization",
        "prompt": (
            "Build a note-taking app that is completely free forever with no paid plans. "
            "The Pro plan at $9.99/month unlocks dark mode, unlimited notes, and collaboration. "
            "The free tier is limited to 10 notes."
        ),
    },
    {
        "id": "e07",
        "category": "edge_conflicting",
        "label": "Conflicting - roles",
        "prompt": (
            "Build a hospital management system. "
            "Doctors should not be able to see billing information. "
            "All medical staff including doctors should have full access to all patient and billing data. "
            "Receptionists handle billing but cannot see patient medical records."
        ),
    },
    {
        "id": "e08",
        "category": "edge_conflicting",
        "label": "Conflicting - auth",
        "prompt": (
            "Make a secure platform where users don't need to log in to access their private data. "
            "Everything is public but only registered users can see it. "
            "No authentication required but all routes are protected."
        ),
    },

    # ── Incomplete ───────────────────────────────────────────────────
    {
        "id": "e09",
        "category": "edge_incomplete",
        "label": "Incomplete - no roles",
        "prompt": (
            "Build a task management app where tasks can be created, assigned, and completed. "
            "Tasks have titles, descriptions, due dates, and priorities."
        ),
    },
    {
        "id": "e10",
        "category": "edge_incomplete",
        "label": "Incomplete - no entities",
        "prompt": (
            "Build a SaaS app with a dashboard, settings page, and admin panel. "
            "Users should be able to log in and see their data."
        ),
    },
]

ALL_PROMPTS = NORMAL_PROMPTS + EDGE_PROMPTS
