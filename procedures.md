# Operational Procedures Guide (Direct Selection)

This guide explains how to manage and use the simplified version of the Dilla Motorcycle Lottery Bot, where users pick numbers and pay directly.

### **1. Admin Procedures**

*   **Step 1: Create a Lottery**
    Send the following command to list a new motorcycle:
    ```text
    /add_lottery Yamaha_R1 "Brand new blue Yamaha motorcycle" 100
    ```
    *(Format: `/add_lottery [Name] [Description] [TicketCount]`)*

*   **Step 2: Verify Transactions**
    When a user uploads a screenshot, you will see the **Lottery Name**, **Ticket Number**, **Phone Number**, and **TX ID**.
    - To **Confirm**: Click the `/approve_ID` link in the message.
    - **Effect**: This marks the ticket as **Confirmed** and notifies the user.

---

### **2. User Procedures**

*   **Step 1: Start**
    Find the bot and press `Start`.

*   **Step 2: Pick a Number**
    1. Click on **🎫 ቲኬት ግዛ**.
    2. Choose the motorcycle lottery you want.
    3. You will see a **Grid of Numbers (1-100)**.
       - Numbers with **❌** are already taken.
       - Numbers with **⏳** are pending (someone else is paying).
    4. Click on an available number to select it.

*   **Step 3: Pay Directly**
    1. Once you pick a number, it will be **Reserved for you for 1 hour**.
    2. Follow the payment instructions (on-screen bank details) to transfer 500 ETB.
    3. **Upload the screenshot** to the bot immediately.

*   **Step 4: Confirmation**
    Once the admin checks your screenshot, you will receive a confirmation message with your fixed ticket number.

*   **Step 5: Provide Details for Prizes**
    To ensure you can receive your prize if you win, provide your full name, bank account, and phone number using the `/setbank` command:
    ```text
    /setbank Full_Name BankAccount PhoneNumber
    ```
    *(Example: `/setbank Abebe_Kebede_Ayele 1000250138533 0911111111`)*

---

### **3. System Features**
- **No Wallet Needed**: Users pay specifically for the number they want.
- **Auto-Release**: If a user picks a number but doesn't send a screenshot within 1 hour, the number becomes available again.
- **One at a Time**: To keep things simple, users can pay for one number at a time.