package com.example.orders;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(value = "/orders", produces = MediaType.APPLICATION_JSON_VALUE)
public class OrderController {

    private final AgeLimitPolicy policy;

    public OrderController(AgeLimitPolicy policy) { this.policy = policy; }

    @PostMapping("/submit")
    public CommandResult submit(OrderContext context) {
        return new CommandResult(policy.permits(context.age()), null);
    }
}
