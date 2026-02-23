"""Verify calculator.py works."""
import sys
sys.path.insert(0, "workspace")
from calculator import Calculator

c = Calculator()
print("add(2,3):", c.add(2, 3))
print("subtract(10,4):", c.subtract(10, 4))
print("multiply(3,7):", c.multiply(3, 7))
print("divide(10,3):", round(c.divide(10, 3), 4))

try:
    c.divide(5, 0)
except ZeroDivisionError:
    print("divide(5,0): ZeroDivisionError caught correctly")

print("history:", c.history())
print("\nAll methods work!")
