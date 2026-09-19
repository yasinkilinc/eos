package com.example.orders;

import org.junit.jupiter.api.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;

// No import of ValidateAgeCommand: it is in the same package, which is how
// almost every test in a real Maven tree refers to its subject.
class ValidateAgeCommandTest {

    @Mock
    private AgeLimitPolicy policy;

    @InjectMocks
    private ValidateAgeCommand command;

    @Test
    void refusesWhenUnderage() {
        CommandResult result = command.execute(new OrderContext(17));
        // Names the code, which is what makes this behaviour findable.
        assertThat(result).hasMessageContaining("AGE_LIMIT");
    }
}
