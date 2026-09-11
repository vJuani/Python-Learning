print("Welcome to Achard Bank")

balance = 1500
attempts = 3
access_granted = False
last_transaction = "No transactions yet"


while attempts > 0:
    pin = int(input("Enter your PIN: "))

    if pin == 1234:
        print("Access granted")
        access_granted = True
        break

    attempts -= 1
    print("Incorrect PIN. You have", attempts, "attempts left.")


def menu():
    print("\n===== ACHARD BANK =====")
    print("1 - Check Balance")
    print("2 - Deposit")
    print("3 - Withdraw")
    print("4 - Last Transaction")
    print("5 - Exit")

    option = int(input("Enter your option: "))
    return option


def show_balance(balance):
    print("Your balance is:", balance)


def deposit_amount(balance):
    amount = int(input("Enter the amount to deposit: "))

    if amount <= 0:
        print("Invalid amount")
        return balance, "No valid transaction"

    balance += amount
    print("Your new balance is:", balance)

    transaction = f"Deposit: ${amount}"
    return balance, transaction


def withdraw_amount(balance):
    amount = int(input("Enter the amount to withdraw: "))

    if amount <= 0:
        print("Invalid amount")
        return balance, "No valid transaction"

    if amount > balance:
        print("Insufficient funds")
        return balance, "Failed withdrawal"

    balance -= amount
    print("Your new balance is:", balance)

    transaction = f"Withdraw: ${amount}"
    return balance, transaction


def show_last_transaction(last_transaction):
    print("Your last transaction was:", last_transaction)


if access_granted:
    option = menu()

    while option != 5:

        if option == 1:
            show_balance(balance)

        elif option == 2:
            balance, last_transaction = deposit_amount(balance)

        elif option == 3:
            balance, last_transaction = withdraw_amount(balance)

        elif option == 4:
            show_last_transaction(last_transaction)

        else:
            print("Invalid option")

        option = menu()

    print("Thank you for using Achard Bank")

else:
    print("Account blocked")