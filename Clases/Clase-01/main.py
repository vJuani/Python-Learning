print("Welcome to Achard Bank")

balance = 1500

pin = int(input("Enter your PIN "))

if pin == 1234:
    print("1 - Check Balance")
    print("2 - Deposit")
    print("3 - Withdraw")
    option = int(input("Enter your option: "))
    if option == 1:
        print("Your balance is: " + str(balance))
    elif option == 2:
        print("Deposit option selected")
        deposit = int(input("Enter the amount to deposit: "))
        balance += deposit
        print("Your new balance is: " + str(balance))

    elif option == 3:
        print("Withdraw option selected")
        withdraw = int(input("Enter the amount to withdraw: "))
        if withdraw > balance:
            print("You have insufficient funds. Your balance is: " + str(balance))
            print("Try again")
        else:
            balance -= withdraw
            print("Your new balance is: " + str(balance))

    else:
        print("Invalid option selected")
 
else:
    print("Access Denied")
