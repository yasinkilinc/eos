package com.example.orders;

import org.springframework.stereotype.Component;

/**
 * Registered under a name and called by nothing: a coordinator looks this up
 * at run time from configuration. No call graph can reach it.
 */
@Component("chainStepCommand")
public class ChainStepCommand implements OrderCommand {

    @Override
    public CommandResult execute(OrderContext context) {
        if (context.age() < 0) {
            throw ValidationException.of("AGE_NEGATIVE", "impossible");
        }
        return new CommandResult(true, null);
    }
}
