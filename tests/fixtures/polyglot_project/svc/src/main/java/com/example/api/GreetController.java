package com.example.api;

import com.example.api.GreetService;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class GreetController {
    private final GreetService service;

    public GreetController(GreetService service) {
        this.service = service;
    }

    public String greet(String name) {
        return service.greet(name);
    }
}
