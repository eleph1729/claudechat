def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


primes = []
n = 2
while len(primes) < 10:
    if is_prime(n):
        primes.append(n)
    n += 1

print(primes)
