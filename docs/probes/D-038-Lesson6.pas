PROGRAM Lesson6;

TYPE
   FighterStance = (Natural, Crane, Tiger, Dragon);
   StatusEffect  = (Poisoned, Shielded, Stunned, Enraged, Hasted);
   StatusSet     = SET OF StatusEffect;

{ The Record structure groups our domains together }
   CombatantRecord = RECORD
			Name         : STRING(10);
			Stance       : FighterStance;
			CurrentHP    : INTEGER;
			Conditions   : StatusSet;
		     END;

VAR
   Player1 : CombatantRecord;

VALUE
   { IBM Pascal allows field-by-field structural initialization }
   Player1.Name       := 'Mr. Karate';
   Player1.Stance     := Natural;
   Player1.CurrentHP  := 100;
   Player1.Conditions := [Shielded];

BEGIN
   WRITELN('--- Combatant Record Training ---');

   { Step 1: Accessing components using dot notation }
   WRITELN('Fighter Profile loaded: ', Player1.Name);
   WRITELN('Health Pool: ', Player1.CurrentHP:1);

   { Step 2: Modifying states deep within the record structural layout }
   Player1.CurrentHP := Player1.CurrentHP - 15;
   Player1.Conditions := Player1.Conditions + [Enraged];

   WRITELN('Post-combat HP: ', Player1.CurrentHP:1);
   
   IF Enraged IN Player1.Conditions THEN
   BEGIN
      WRITELN('Fighter state: Enraged counter-attack imminent!');
   END;
END.
