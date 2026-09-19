package com.example.orders;

import org.springframework.stereotype.Service;

@Service
public class AgeLimitPolicy {
    public boolean permits(int age) { return age >= 18; }
}
