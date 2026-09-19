package com.example.orders;

import org.springframework.stereotype.Component;

/**
 * Package-private on purpose, and the whole class body sits inside a Javadoc
 * example below that contains a closing brace: } and the word class.
 */
@Component("validateAgeCommand")
class ValidateAgeCommand implements OrderCommand {

    private final AgeLimitPolicy policy;

    ValidateAgeCommand(AgeLimitPolicy policy) {
        this.policy = policy;
    }

    @Override
    public CommandResult execute(OrderContext context) {
        if (!policy.permits(context.age())) {
            return new CommandResult(false, "AGE_LIMIT");
        }
        return new CommandResult(true, null);
    }

    static final class Nested {
        void helper() { }
    }
}
